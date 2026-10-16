# Instalación y uso en Windows

> **Estado de verificación:** estas instrucciones se redactaron a partir de la documentación de Docker/WSL2 y de la
> arquitectura del proyecto. **No pudieron probarse en un Windows real** (el desarrollo se verificó en Linux sin Docker).
> Si un paso falla, consulte `troubleshooting.md`.

## Flujo recomendado (uno solo, para no instalar herramientas redundantes)

**Docker Desktop (backend WSL2) + una distribución Ubuntu en WSL2. Todos los comandos del proyecto se ejecutan en la
terminal de Ubuntu (WSL2)**, con el repositorio clonado *dentro* del sistema de archivos de WSL (`~/prueba-postgresql`),
no en `C:\`. Motivo: los scripts son Bash, Python y `docker` funcionan igual que en Linux y el rendimiento de disco de
`C:\` montado en WSL2 es mucho peor (afectaría a los resultados).

| Dónde se escribe el comando | Qué se usa allí |
|---|---|
| **PowerShell (Windows)** | Solo la instalación inicial (`wsl --install`, `.wslconfig`). |
| **Ubuntu en WSL2** | Todo lo demás: Git, Python, `scripts/*.sh`, `python -m src....`, `docker compose`. |
| **Dentro de un contenedor** | Lo gestiona el proyecto (`postgres`, `pgbouncer`, `loadgen`). No necesita escribir nada allí, salvo depuración (`docker compose exec ...`). |

Alternativa solo-PowerShell: el orquestador es Python y solo invoca `docker`, así que `python -m src.ejecutar_experimentos ...`
funciona en PowerShell; pero los `scripts/*.sh` no (use los comandos `python -m` equivalentes) y `cp` se reemplaza por `Copy-Item`.
No está probado y no se recomienda.

## 1. Requisitos

* **Hardware (perfil `smoke`/`reducido`)**: 4 CPU lógicas, 8 GB de RAM, 10 GB libres.
* **Hardware (perfil `paper`)**: ≥ 9 CPU lógicas dedicadas a Docker, ≥ 8 GB de RAM para Docker (PostgreSQL 4 GB + PgBouncer 0,5 GB + generador + sistema), ~15 GB de disco. Un portátil habitual **no** lo cumple.
* **Sistema**: Windows 10 (22H2) o Windows 11, 64 bits, virtualización activada en BIOS/UEFI.

> **Importante:** en Windows los contenedores corren dentro de una VM (WSL2). Aunque use el perfil `paper`, los resultados
> **no son comparables con los del paper** (el proyecto lo marca `comparable_con_paper=false`): no hay un kernel 6.5 dedicado, los
> `cpuset` apuntan a CPU virtuales y el planificador de Windows interviene.

## 2. Instalar WSL2 y Ubuntu (PowerShell como administrador)

```powershell
wsl --install -d Ubuntu-24.04
wsl --set-default-version 2
```
Reinicie si se pide, cree usuario/contraseña de Ubuntu y compruebe: `wsl -l -v` (debe mostrar VERSION 2).

Para dar CPU/RAM a la VM cree `C:\Users\<usuario>\.wslconfig`:
```ini
[wsl2]
processors=9     # 4 para PostgreSQL + 1 PgBouncer + 4 generador (perfil paper). Con 4 CPU use processors=4
memory=10GB
swap=0
```
y ejecute `wsl --shutdown`.

## 3. Instalar Docker Desktop
1. Descargue Docker Desktop desde docker.com e instálelo con la opción **WSL 2 backend**.
2. *Settings → Resources → WSL integration*: active **Ubuntu-24.04**. Aplique y reinicie.

## 4. Git y Python (dentro de Ubuntu/WSL2)
```bash
sudo apt update
sudo apt install -y git python3 python3-pip python3-venv
python3 --version      # se necesita >= 3.11 (Ubuntu 24.04 trae 3.12)
```
No hace falta instalar `psql` ni `pgbench`: se usan los de la imagen `postgres:16.4`.

## 5. Obtener el proyecto y dependencias
```bash
cd ~
git clone <URL-del-repositorio> prueba-postgresql     # o copie la carpeta dentro de WSL
cd prueba-postgresql
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 6. Variables de entorno
```bash
cp .env.example .env
nano .env        # cambie las 3 contraseñas (son locales; .env está en .gitignore)
```
Ajuste CPU a su `.wslconfig`. Con 4 CPU, use el bloque “reducido” del archivo (PG 0-1, PgBouncer 2, generador 3). Los
`*_CPUSET` deben existir en la VM; si no, Docker responde *“Invalid value ... for cpuset”*.

## 7. Verificar
```bash
docker --version && docker compose version
docker info --format '{{.NCPU}} CPU, {{.MemTotal}} bytes'     # compruebe que coincide con .wslconfig
python3 -m pytest tests -q                                      # pruebas sin Docker
python3 -m src.configuracion --validar
```

## 8. Inicializar
```bash
scripts/inicializar.sh smoke       # perfil rápido: escala 1
# scripts/inicializar.sh reducido  # escala 10
# scripts/inicializar.sh paper     # escala 50: 5.000.000 filas, ~1-3 min y ~750 MB
docker compose ps                  # postgres y pgbouncer deben figurar "healthy"
docker compose exec postgres psql -U pgadmin -d bench -c "SELECT version();"
docker compose exec pgbouncer pgbouncer --version
```
(La base se inicializa con la escala del perfil indicado; para cambiar de escala use `scripts/limpiar.sh --volumenes` y reinicialice.)

## 9. Ejecutar
```bash
scripts/ejecutar_vector1.sh smoke          # solo Vector 1
scripts/ejecutar_vector2.sh smoke          # solo Vector 2
scripts/ejecutar_todo.sh reducido          # ambos
```
Muestran un plan y piden confirmación. Detalles en `guia_de_ejecucion.md`.

## 10. Resultados y PDF
Los archivos quedan en `~/prueba-postgresql/resultados/`. Para abrirlos desde Windows: en el Explorador escriba
`\\wsl$\Ubuntu-24.04\home\<usuario>\prueba-postgresql\resultados`.
```bash
python -m src.generar_pdf --ultima-ejecucion       # -> resultados/pdf/informe_<run_id>.pdf
```

## 11. Métricas en Windows
Todas las métricas de recursos se leen **dentro de los contenedores** (cgroup), así que funcionan en WSL2. Excepción:
*cambios de contexto por segundo* son de la VM de WSL2 completa (no solo de PostgreSQL). Si el cgroup no estuviera disponible, se
muestra `N/D` con el motivo.

## 12. Detener y limpiar
```bash
scripts/limpiar.sh                 # detiene contenedores
scripts/limpiar.sh --volumenes     # además borra la base
scripts/limpiar.sh --resultados    # además borra CSV/gráficos/PDF (pide confirmación)
```

## 13. Errores frecuentes en Windows
| Síntoma | Causa | Solución |
|---|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop no está iniciado o falta la integración WSL | Inicie Docker Desktop; *Settings → WSL integration → Ubuntu* |
| `Invalid value "4" for cpuset` | La VM tiene menos CPU | Suba `processors` en `.wslconfig` o use el bloque “reducido” de `.env` |
| `bash: scripts/x.sh: /bin/bash^M: bad interpreter` | Fin de línea CRLF por clonar con Git de Windows | Clone dentro de WSL, o `git config core.autocrlf input` y vuelva a clonar |
| `port is already allocated` (5432/6432) | Otro PostgreSQL en Windows | Cambie `PG_HOST_PORT` en `.env` o detenga el servicio |
| Contenedor `Killed` / OOM | `memory` de `.wslconfig` insuficiente | Aumente `memory` o use perfil `reducido` (`PG_MEM=2g`) |
| Muy lento | Repositorio en `C:\` | Muévalo a `~/` dentro de WSL |
