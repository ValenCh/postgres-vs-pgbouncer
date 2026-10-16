# Métricas, resultados e interpretación

## Definiciones
| Métrica | Definición exacta | Fuente |
|---|---|---|
| **TPS** | transacciones exitosas en la ventana de medición ÷ duración (s). V2: solo carga legítima. | generador |
| **Latencia P95** | percentil 95 (interpolación lineal) de la latencia de los intentos **exitosos**: desde el `BEGIN` hasta confirmar el `COMMIT`; excluye la pausa voluntaria y la conexión. Si no hubo éxitos: `N/D`. | generador |
| `p95_con_conexion_ms` | Igual, pero incluyendo el tiempo de conexión en el mismo intento (relevante en B, donde la espera de cola ocurre al conectar). | generador |
| **Tasa de rechazo** | `fallidas ÷ (exitosas + fallidas) × 100`. Cada intento fallido cuenta **una** vez. Sin intentos → `N/D`. | generador |
| **Error de ejecución** | Fallo del generador/entorno (sin descriptores, sin puertos efímeros, autenticación, bug). **No** es rechazo, **no** entra al denominador; si supera el 1 % de los intentos, el registro queda `invalida`. | generador |
| **RAM** | working set del cgroup del contenedor PostgreSQL (`memory.current − inactive_file`, igual que `docker stats`); media en la ventana y pico. | sonda |
| **%Sys** | Δ`system_usec` del cgroup ÷ (Δt × vCPU asignadas) × 100. Solo PostgreSQL. | sonda |
| **CS/s** | Δ`ctxt` de `/proc/stat` ÷ Δt. **Del host/VM completo** (incluye generador y PgBouncer). | sonda |
| **Conexiones** | clientes en `pg_stat_activity`; `idle_in_tx` = los que están en `idle in transaction`. Media y pico (muestreo cada 5 s). | sonda |
| **Efectividad de defensa** | TPS(X) ÷ TPS(D1) y (rechazo D1 − rechazo X) en puntos porcentuales, dentro de la misma ejecución. | análisis |

## Estadísticos con varias repeticiones
Por (vector, escenario, clientes) se usan solo repeticiones `ok`: media, desviación muestral (n−1), mín., máx. y CV %. Con n = 1 no hay dispersión (se rotula `n=1`).
**Los percentiles no se promedian a ciegas.** Se informan `p95_ms_media` (media de los P95 de cada ejecución, la convención del paper, útil para compararse con él) y
`p95_ms_agregado` (P95 de todas las latencias de todas las repeticiones, leídas de `data/raw/.../latencias.csv.gz`), que es el estadístico correcto del punto. El rechazo agregado es Σfallidas ÷ Σintentos.

## Archivos
* `resultados/csv/<run_id>.csv` — una fila por punto/repetición. Campos: `run_id, fecha_hora_utc, perfil, comparable_con_paper, vector, escenario, repeticion, clientes, atacantes, duracion_s, calentamiento_s, tps, p50_ms, p95_ms, p99_ms, p95_con_conexion_ms, ok, fallidas, total_solicitudes, tasa_rechazo_pct, errores_ejecucion, ram_mb_media, ram_mb_pico, ram_pgbouncer_mb, cpu_sys_pct, cs_por_s, conexiones_pg_media, conexiones_pg_pico, idle_in_tx_pico, version_pg, version_pgbouncer, sistema, recursos, estado, observaciones, ruta_raw`.
* `data/raw/<run_id>/v<V>/<ESC>/c<N>_r<R>/` — `resumen_carga.json`, `latencias.csv.gz`, `series_por_segundo.csv`.
* `data/processed/<run_id>/` — `resumen_vector{1,2}.csv`, `comparacion_paper.csv`.
* `estado`: `ok` (válido) · `invalida` (falló alguna validación: sumas, rangos, hilos no lanzados...) · `fallida` (error de infraestructura) · `interrumpida`.

## Disponibilidad por plataforma
Linux nativo: todas. Docker Desktop/WSL2/macOS: todas las del cgroup; CS/s corresponde a la VM. Si un cgroup no expone un dato → `N/D` + motivo en `observaciones`. Nunca se sustituye por 0.

## Cómo comparar con el paper
`python -m src.comparar_paper --ultima-ejecucion` lista referencia, medido, diferencia absoluta y porcentual (`N/D` si la referencia es 0).
Las referencias (`config/referencia_paper.json`) son **valores publicados**, no mediciones de este proyecto; se transcribieron de las Tablas 2–5 y fueron comprobadas contra el PDF
(p. ej. V1 con 2000 clientes: A 29 TPS / 14820,2 ms / 94,8 % / 2010 MB; C 795 TPS / 2890,6 ms / 1,2 % / 1440 MB; V2 según Tabla 3).
Si la ejecución es `smoke`/`reducido`, tiene otro hardware o Docker Desktop, **la diferencia no demuestra que el paper esté equivocado**.
Lo que sí es comparable aun en equipos distintos son las **relaciones cualitativas**: orden C > B > A en TPS a alta concurrencia, rechazo creciente de A, y si D1 < A en el V2.

## Validez: condiciones que la afectan
CPU compartida entre servicios (sin `cpuset` válidos), otros procesos en el host, disco lento (WAL/checkpoints), generador saturado (GIL: revise `%` de CPU del contenedor `loadgen`),
`errores_ejecucion` > 0, throttling térmico, pocas repeticiones, y calentamiento insuficiente.
