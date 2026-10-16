# Resolución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| `Falta .env` / `Falta POSTGRES_PASSWORD` | No copió `.env.example` | `cp .env.example .env` y edite contraseñas |
| `permission denied ... /var/run/docker.sock` | Usuario fuera del grupo `docker` | `sudo usermod -aG docker $USER` + reabrir sesión (ver riesgos en `instalacion_linux.md`) o usar `sudo` |
| `Invalid value "4" for cpuset` / `invalid CPUset` | Docker/VM con menos CPU que `*_CPUSET` | Ajuste `.env` (bloque “reducido”) o suba CPU de la VM (`.wslconfig`/Docker Desktop) |
| `El puerto 5432 (PostgreSQL) ya está en uso` | Otro PostgreSQL local | Detenerlo o cambiar `PG_HOST_PORT`/`PGB_HOST_PORT` en `.env` |
| Servicio `unhealthy` / no llega a `healthy` | Error de configuración o memoria | `docker compose logs postgres pgbouncer`; PgBouncer: revise `runtime/pgbouncer/pgbouncer.ini` |
| La imagen de PgBouncer no construye (“grep -q PgBouncer 1.22”) | La distro base trae otra versión | No cambie la base; el chequeo existe para garantizar 1.22 |
| `La base tiene escala X pero el perfil requiere Y` | Inicializó con otro perfil | `scripts/inicializar.sh <perfil>` (re-ejecuta `pgbench -i`) |
| `PostgreSQL: shared_buffers = ...` en preflight | `postgresql.conf` no montado o editado | Verifique el volumen y `config/postgres/postgresql.conf` |
| `postgres` muere / `Killed` (OOM) | `PG_MEM` mayor que la RAM de Docker | Aumente la memoria de Docker/WSL2 o use `reducido` |
| `FATAL: password authentication failed` (errores de ejecución) | Contraseñas de `.env` distintas de las del volumen existente | Los roles se crean solo la primera vez: `scripts/limpiar.sh --volumenes` y reinicialice |
| `Too many open files` / `Cannot assign requested address` en `errores_ejecucion` | Descriptores o puertos efímeros agotados en el generador | Registro `invalida`. Revise `ulimits`/`sysctls` de `loadgen` o reduzca clientes |
| `el generador solo pudo lanzar N/M hilos` | Memoria/límite de hilos del contenedor `loadgen` | Más RAM o menos clientes (registro `invalida`) |
| Muchos `N/D` en RAM/%Sys | cgroup no expuesto en el contenedor | `docker compose exec postgres sh /sonda/sonda_recursos.sh`; revise `observaciones` |
| `0 solicitudes exitosas: P95 N/D` | Colapso total (puede ser un resultado legítimo, p. ej. D1) o servicio caído | Revise `fallos_por_tipo` en `observaciones` |
| `Entrada no interactiva: use --si` | Ejecución sin terminal | Añada `--si` |
| PDF: `No hay resultados válidos` | Todos los registros están `invalida/fallida` | `python -m src.analizar_resultados --run-id <id>` muestra los motivos |
| Quedó una ejecución a medias | Interrupción/caída | `--reanudar <run_id>` (ver guía) |
| Contenedores/transacciones colgados tras un fallo | Salida abrupta | `scripts/limpiar.sh` (o `docker compose restart`); el orquestador también termina backends antes y después de cada punto |
| `bad interpreter: /bin/bash^M` | CRLF de Windows | Clone dentro de WSL o `dos2unix scripts/*.sh` |
| Resultados muy inferiores a los del paper | Hardware/VM/generador Python saturado | Esperable; vea `limitaciones.md` y revise la CPU del contenedor `loadgen` con `docker stats` |

Logs: `logs/<run_id>.log`; crudo del generador: `data/raw/<run_id>/.../resumen_carga.json` (campos `fallos_por_tipo`, `ejemplos_error`, `ataque`).
