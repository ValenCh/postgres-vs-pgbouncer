# Limitaciones, supuestos y observaciones sobre el paper

## 1. Qué se verificó y qué no (estado del desarrollo)
| Componente | Estado |
|---|---|
| 51 pruebas unitarias (`pytest tests`) | **Ejecutadas, pasan** (sin Docker) |
| Generador de carga (V1 y V2, rechazos, atacantes, host-guard) | **Ejecutado** contra PostgreSQL 16.15 y PgBouncer 1.22.0 locales (apt), a escala mínima |
| `pgbouncer.ini` generado (todos los escenarios renderizados; D3b/D3c, `idle_transaction_timeout=0.5`, cuota 15, SCRAM) | **Validado** con PgBouncer 1.22.0 real (`SHOW CONFIG`, `SHOW POOLS`) |
| Análisis, comparación con el paper, gráficos y PDF | **Ejecutados** y PDF inspeccionado visualmente |
| `docker-compose.yml`, Dockerfiles, `ejecutar_experimentos.py` (capa Docker), sonda en cgroup | **Solo verificación estática** (sintaxis); **no ejecutados** (el entorno de desarrollo no tenía Docker) |
| Ningún experimento del paper se ejecutó a escala real | Las cifras del paper **no están reproducidas** por este desarrollo |

Es esperable que la primera ejecución real en Docker requiera algún ajuste menor. Empiece siempre con `smoke`.

## 2. Parámetros del paper que no eran válidos tal cual
Ver tabla en `metodologia_experimental.md`: `idle_transaction_timeout` no existe en PostgreSQL 16 (sí en PgBouncer, en segundos); el `default_pool_size` del paper no se menciona y condiciona si el pool de 40 es alcanzable.

## 3. Supuestos propios (el paper no los especifica) — configurables en `config/experimento.toml`
1. **Pausa de los legítimos en V2**: U(50,500) ms (`v2.pausa_legit_ms`).
2. **Reintento tras un rechazo**: espera U(50,500) ms (`carga.reintento_pausa_ms`). Sin espera, el motor nativo recibiría millones de intentos fallidos y la tasa de rechazo cambiaría.
3. **Atacante tras ser cortado**: el paper dice que el bloque `except` “reabre de inmediato”, pero con `time.sleep(10)` no se lanza ninguna excepción hasta la siguiente operación. Se implementan dos modos: `detectar_cierre` (por defecto, vigila el socket y reconecta al instante; refleja la intención) y `sleep_literal` (duerme los 10 s completos). **Cambian mucho D2/D3a/D3c**, por lo que se recomienda correr ambos y reportar la diferencia (edite `v2.modo_ataque`; el modo queda registrado en `observaciones`).
   Con `sleep_literal`, un corte de 5 s libera el servidor, pero el atacante tarda 10 s en reaccionar.
4. Pausa entre intentos del atacante tras un fallo: 100 ms.
5. `default_pool_size = 40`; `min_pool_size = 20` y `query_wait_timeout = 30 s` según el paper.
6. Sin `-C` real: los efímeros se emulan con conexiones nuevas en Python (el costo de SCRAM/`fork` es equivalente, no idéntico, al de `pgbench -C`).
7. El **P95** excluye el tiempo de conexión (como el paper); en B la espera de cola ocurre al conectar, por eso se guarda también `p95_con_conexion_ms`.

## 4. Limitaciones técnicas
* **Generador en Python** (`threads` + GIL, un único proceso): con 2000 hilos puede ser un cuello de botella antes que el sistema probado; el contenedor `loadgen` tiene 4 vCPU pero un proceso Python usa efectivamente ~1. Si el generador se satura, el TPS medido estará **por debajo** del real y la latencia **sobreestimada**. Mitigación posible (no implementada): repartir los clientes en varios procesos.
* **Cambios de contexto**: se miden en `/proc/stat` del host/VM completo; el paper no aclara el alcance. No son estrictamente comparables.
* **%Sys**: solo del contenedor de PostgreSQL (no PgBouncer).
* **RAM**: working set del cgroup (incluye `shared_buffers` tocados y `shmem`); el paper habla de memoria “de procesos del motor”, sin precisar RSS/USS/cgroup.
* Muestreo cada 5 s (pocos puntos en ventanas cortas; en `smoke` el %Sys/CS pueden salir `N/D`).
* **No se implementó** la prueba complementaria del paper con `max_connections=2000` y OOM Killer (Sec. 4.3), por riesgo para el host. Puede hacerse a mano editando `postgresql.conf`.
* PgBouncer es monohilo (1 vCPU): puede saturarse con SCRAM a 2000 clientes — es parte de lo medido.
* Sin TLS ni latencia WAN, como en el paper.
* Docker Desktop/WSL2/macOS: VM intermedia, kernel distinto, `cpuset` sobre CPU virtuales → **nunca** se marca comparable.
* `data/raw` queda con propietario root en Linux (escribe el contenedor).

## 5. Observaciones sobre la consistencia interna del paper (solo informativas)
* La caída de A entre 50 y 2000 clientes se cita como 95,2 %; con sus propias tablas (612→29) resulta 95,3 %.
* La latencia media de C (2320 ms) no se obtiene exactamente de la fórmula citada (2000/795 − Z ≈ 2241 ms con Z = 275 ms).
* Los dos nombres `idle_in_transaction_session_timeout` / `idle_transaction_timeout` y la etiqueta “PG/PgB 500ms” en D3a son ambiguos (ver §2).
* El P95 de A con 2000 clientes (14820 ms) proviene de “las pocas transacciones que se completan”: sesgo de supervivencia (con 94,8 % de rechazo, el P95 de los exitosos no describe a los demás).
No invalidan por sí solas el trabajo; se señalan para que quien reproduzca sepa dónde mirar.

## 6. Seguridad
Puertos publicados solo en `127.0.0.1`; el generador solo conecta a `postgres`/`pgbouncer`/`localhost`/`127.0.0.1` **y** exige que resuelvan a IP loopback/privada (verificado: host externo → exit 3). No existe parámetro para apuntar a otro destino salvo editar el código/lista blanca. Las credenciales de `.env` son de laboratorio; `runtime/pgbouncer/userlist.txt` contiene contraseñas en claro (0644) y está en `.gitignore`.
