# Arquitectura

```text
                         Host (Linux / WSL2)
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ python -m src.ejecutar_experimentos   (orquestador; solo usa el CLI docker)│
  │   ├─ configuracion.py  ← config/experimento.toml, .env                      │
  │   ├─ renderiza runtime/pgbouncer/{pgbouncer.ini,userlist.txt}               │
  │   ├─ recolectar_metricas.py ── docker exec ──► sonda_recursos.sh (cgroup)   │
  │   └─ escribe resultados/csv/<run>.csv y data/raw/<run>/...                  │
  └───────┬──────────────────────────────────────────────────────────────────┘
          │ docker compose (red interna; puertos solo en 127.0.0.1)
  ┌───────▼───────────┐  TCP 6432   ┌───────────────┐  TCP 5432  ┌──────────────┐
  │ loadgen (4 vCPU,  │────────────►│ pgbouncer 1.22│───────────►│ postgres 16.4│
  │ cpuset 5-8)       │   (B,C,D*)  │ 1 vCPU, 512MB │  (B,C,D*)  │ 4 vCPU, 4 GB │
  │ generador_carga.py│─────────────────────────────────────────►│ cpuset 0-3   │
  └───────────────────┘                TCP 5432 (escenario A)    └──────────────┘
```

**Flujo de datos de un punto experimental:** (1) se aplican los ajustes del escenario (`ALTER ROLE ... SET` y/o `pgbouncer.ini`) y se reinicia PgBouncer;
(2) se limpia el estado de la BD; (3) arranca el muestreador y el generador (dentro de `loadgen`); (4) el generador escribe `resumen_carga.json`,
`latencias.csv.gz` y `series_por_segundo.csv`; (5) el orquestador combina resumen + métricas de recursos, **valida** el registro y lo añade al CSV;
(6) `analizar_resultados` agrega → `comparar_paper` → `generar_graficos` → `generar_pdf`.

## Módulos (una responsabilidad cada uno)
| Módulo | Responsabilidad |
|---|---|
| `utilidades.py` | Estadística (percentil, desviación, tasa), clasificación de errores, guardián de hosts, columnas del CSV. Solo stdlib (corre también en `loadgen`). |
| `configuracion.py` | Carga/validación del TOML, generación de `pgbouncer.ini`, `comparable_con_paper`. |
| `generador_carga.py` | Carga TPC-B, clientes efímeros/persistentes, atacantes idle-in-transaction, contabilidad de rechazos. |
| `recolectar_metricas.py` | Parseo de la sonda y cálculo de RAM/%Sys/CS/conexiones. |
| `ejecutar_experimentos.py` | Preflight, plan, confirmación, ejecución, reanudación, interrupción segura. |
| `analizar_resultados.py` / `comparar_paper.py` | Validación, agregación, comparación con `config/referencia_paper.json`. |
| `generar_graficos.py` / `generar_pdf.py` / `presentacion.py` | Presentación. No calculan resultados. |

## Decisiones técnicas
* **PostgreSQL `postgres:16.4`** (oficial, versión del paper); **PgBouncer 1.22.0** construido desde Ubuntu 24.04, que empaqueta esa versión exacta (el `Dockerfile` falla si no coincide).
* **Una sola configuración** (`experimento.toml` + `.env`); PgBouncer se **genera** por escenario, no hay 8 `.ini` duplicados.
* **Sonda dentro del contenedor** para que Windows/WSL2/macOS midan igual que Linux.
* **Python 3.11+ con `tomllib`**: evita una dependencia YAML.
* `pgbench` solo para inicializar (`-i -s`); el generador propio por las razones de `metodologia_experimental.md`.
* **Seguridad**: lista blanca + resolución privada + puertos en loopback.
