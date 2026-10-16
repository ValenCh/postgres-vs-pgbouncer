# Instalación y uso en Linux

> **Estado de verificación:** los módulos Python, la generación de configuraciones PgBouncer, el generador de carga y
> el PDF se probaron en Ubuntu 24.04 contra PostgreSQL 16.15 y PgBouncer 1.22.0 instalados con `apt` (sin Docker).
> La parte **Docker Compose no pudo ejecutarse** en el entorno de desarrollo: se validó solo estáticamente (YAML, tests).

## 1. Distribuciones y requisitos
Cualquier distribución con Docker Engine ≥ 24 y **cgroup v2** (Ubuntu ≥ 22.04, Debian ≥ 12, Fedora ≥ 31). Para replicar el
paper (Docker 25.0, kernel 6.5) conviene un kernel ≥ 6.5 (Ubuntu 24.04 HWE). Perfil `paper`: ≥ 9 CPU lógicas, ≥ 8 GB RAM, ~15 GB de disco.

```bash
nproc; free -h; df -h .; uname -r
stat -fc %T /sys/fs/cgroup        # "cgroup2fs" = cgroup v2
```
Para obtener resultados **comparables** use un equipo dedicado: sin otras cargas, con el gobernador de CPU en `performance`
y, idealmente, `isolcpus` para los núcleos del experimento.

## 2. Docker Engine y Compose
Debian/Ubuntu (repositorio oficial de Docker; verifique la guía vigente en docs.docker.com/engine/install):
```bash
sudo apt update && sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update && sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```
Fedora: `sudo dnf install -y docker-ce docker-compose-plugin` (tras añadir el repo oficial) y `sudo systemctl enable --now docker`.
Arch: `sudo pacman -S docker docker-compose`.

**Permisos:** `sudo usermod -aG docker $USER` y reabra sesión. **Riesgo:** pertenecer al grupo `docker` equivale a
tener root en la máquina (puede montar `/` en un contenedor). En equipos compartidos use `sudo docker ...` o Docker rootless.

## 3. Python (≥ 3.11) y dependencias
```bash
sudo apt install -y python3 python3-pip python3-venv git        # Fedora: dnf install python3 python3-pip git
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
`psql` y `pgbench` **no** hacen falta en el host: se ejecutan dentro del contenedor `postgres`. La monitorización la hace
`scripts/sonda_recursos.sh` dentro de los contenedores (no se requieren `sysstat`, `pidstat` ni `perf`).

## 4. Clonar y configurar
```bash
git clone <URL-del-repositorio> prueba-postgresql && cd prueba-postgresql
cp .env.example .env && nano .env      # cambie las 3 contraseñas
```
Con menos de 9 CPU use el bloque “reducido” de `.env` (cpusets distintos y existentes).

## 5. Verificar
```bash
docker --version; docker compose version
python3 -m pytest tests -q
python3 -m src.configuracion --validar
```

## 6. Inicializar, ejecutar, resultados
```bash
scripts/inicializar.sh paper               # o smoke / reducido; construye imágenes y ejecuta pgbench -i -s <escala>
docker compose ps
scripts/ejecutar_vector1.sh paper          # ~2,7 h con 3 repeticiones
scripts/ejecutar_vector2.sh paper          # ~0,8 h
scripts/ejecutar_todo.sh paper             # ~3,5 h
python -m src.generar_pdf --ultima-ejecucion
```
Resultados: `resultados/csv/`, `resultados/graficos/<run_id>/`, `resultados/pdf/`, brutos en `data/raw/<run_id>/`, procesados en `data/processed/<run_id>/`.
Los archivos de `data/raw` los crea el contenedor como root; si necesita borrarlos use `sudo` o `scripts/limpiar.sh --resultados`.

## 7. Limpieza
`scripts/limpiar.sh` (detiene), `--volumenes` (borra la BD), `--resultados` (borra resultados).

## 8. Errores frecuentes
Véase `troubleshooting.md`. Los más comunes: `permission denied` en `/var/run/docker.sock` (falta el grupo `docker`),
`Invalid value for cpuset` (menos CPU que las indicadas en `.env`), puertos 5432/6432 ocupados (otro PostgreSQL local), y
`cgroup v1` (la sonda tiene respaldo, pero verifique que RAM/%Sys no salgan `N/D`).
