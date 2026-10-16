# Metodología experimental

Reproducción del diseño de la Sección 3 del paper *“Evaluación Empírica de la Resiliencia en la Capa de Conexión de PostgreSQL ante
Ataques de Denegación de Servicio mediante Multiplexación Transaccional”* (Chiappini, Marini, Oyarzo, Sánchez – UTN FRLP).

## Objetivos e hipótesis
* **H1 (Vector 1):** con inundación de conexiones y pausas fuera del bloque transaccional, el modo `transaction` mantiene disponibilidad > 98 % hasta 20 × `max_connections`.
* **H2 (Vector 2):** ante retención de transacciones abiertas, un pool transaccional reducido se satura antes que el motor nativo; el corte subsegundo + cuota por rol aíslan el vector.

El proyecto **mide**; no asume que las hipótesis se cumplan. Las conclusiones del informe salen solo de los datos.

## Variables
| Tipo | Vector 1 | Vector 2 |
|---|---|---|
| Independientes | Escenario (A, B, C) y clientes {50,100,250,500,1000,2000} | Configuración (A, D1, D2, D3a, D3b, D3c) |
| Controladas | PostgreSQL 16.4 (imagen fija), PgBouncer 1.22.0, `shared_buffers` 1024 MB, `work_mem` 4 MB, `max_wal_size` 2 GB, `checkpoint_completion_target` 0,9, `max_connections` 100, escala 50, límites de CPU/RAM, mezcla 30 % efímeros / 70 % persistentes, pausa U(50,500) ms, duración 120 s + 30 s de calentamiento, SCRAM-SHA-256, sin TLS | 500 clientes `app_core`, 60 atacantes `app_portal`, espera de 10 s, 120 s |
| Dependientes | TPS, P95, rechazo, RAM, CS/s, %Sys | TPS legítimo, P95, rechazo legítimo, conexiones abiertas, idle-in-tx, efectividad de defensa |
| Repeticiones | 3 por punto | 3 por configuración |

## Configuraciones
| Esc. | Ruta | PgBouncer | Defensas |
|---|---|---|---|
| A | cliente → PostgreSQL:5432 | — | ninguna (97 ranuras útiles) |
| B | cliente → PgBouncer:6432 | `session`, `max_db_connections=80`, `max_client_conn=2500` | — |
| C / D1 | idem | `transaction`, `max_db_connections=40` | ninguna |
| D2 | idem | como D1 | PostgreSQL `idle_in_transaction_session_timeout=5000ms` (rol `app_portal`) |
| D3a | idem | como D1 + `idle_transaction_timeout=0.5` (s) | corte de 500 ms (PgBouncer) |
| D3b | idem | como D1 + `[users] app_portal: max_user_connections=15` | cuota por rol |
| D3c | idem | D3a + D3b | ambas |
| D3a-PG *(extra, opcional)* | idem | como D1 | PostgreSQL `idle_in_transaction_session_timeout=500ms` |

Comunes de PgBouncer: `query_wait_timeout=30`, `min_pool_size=20`, `default_pool_size=40` (ver limitaciones), auth SCRAM.

### Validación de parámetros del paper (hecha contra PgBouncer 1.22.0 y PostgreSQL 16)
| Nombre en el paper | Hallazgo | Decisión |
|---|---|---|
| `idle_in_transaction_session_timeout` (D2) | Existe en PostgreSQL (ms). | Se aplica con `ALTER ROLE app_portal SET ...`. |
| `idle_transaction_timeout = 500ms` (D3a/D3c) | **No existe en PostgreSQL 16** (llega con PG 17). **Sí existe en PgBouncer** (en segundos; `0.5` aceptado en 1.22.0). | Se usa el de PgBouncer. La tabla del paper dice “PG/PgB”, de modo que se ofrece además la variante PG (`D3a_pg`). |
| `max_user_connections = 15` | Existe en PgBouncer, global o por usuario en `[users]`. | Por usuario, solo `app_portal`. Verificado: 15 conexiones físicas + 15 clientes en espera. |
| `max_db_connections` en `[databases]` | Válido. | Tal cual. |
| Pool “de 40 conexiones” | `default_pool_size` (por par db/usuario) vale 20 por defecto; con él el rol legítimo nunca usaría 40. | `default_pool_size=40`, de modo que `max_db_connections` sea el límite efectivo. |

## Carga y contabilidad
* Transacción TPC-B de pgbench (3 UPDATE, 1 SELECT, 1 INSERT), enviada sentencia a sentencia.
* **V1**: el 30 % de los clientes abre/cierra una conexión por transacción (equivale a `pgbench -C`); el 70 % mantiene conexión y hace una pausa U(50,500) ms (`\sleep`) *fuera* de BEGIN…COMMIT.
* **V2**: legítimos persistentes con la misma pausa (supuesto); 60 hilos atacantes: conectar → `BEGIN; SELECT abalance ...` → esperar 10 s sin `COMMIT` → reconectar.
* **Intento** = [conectar] + BEGIN…COMMIT. Éxito → una latencia. Fallo (error de conexión, `too many clients`, `query_wait_timeout`, corte...) → un rechazo, tras lo cual el cliente espera U(50,500) ms y reintenta (supuesto).
* Solo cuentan los intentos que terminan dentro de la ventana de medición. Los 30 s de calentamiento se descartan.
* **Por qué no `pgbench` para la carga:** aborta clientes cuyo socket se cierra y no cuenta intentos fallidos; el paper define el rechazo por petición y excluye la pausa del P95. `pgbench -i` sí se usa para inicializar.

## Equidad de la comparación
Misma imagen y configuración de PostgreSQL, base reiniciada (`TRUNCATE pgbench_history`, `CHECKPOINT`, conexiones terminadas) y PgBouncer reiniciado antes de **cada** punto; mismo generador, semilla de pausas por hilo y límites de recursos; `cpuset` disjuntos (PostgreSQL / PgBouncer / generador); orden de ejecución: repetición → vector → escenario → nivel (las repeticiones quedan separadas en el tiempo).

## Lo que impide una reproducción exacta
Hardware y kernel distintos, generador propio (Python/`psycopg2` con GIL) en lugar del no publicado, supuestos del paper no especificados (pausa de legítimos en V2, política de reintento, pool por rol), Docker Desktop/WSL2 en Windows. Ver `limitaciones.md`.
