#!/usr/bin/env bash
# Levanta los contenedores e inicializa pgbench. Uso: scripts/inicializar.sh [smoke|reducido|paper]
set -euo pipefail
cd "$(dirname "$0")/.."
PERFIL="${1:-smoke}"
[ -f .env ] || { echo "Falta .env: ejecute  cp .env.example .env  y cambie las contraseñas."; exit 1; }
exec python3 -m src.ejecutar_experimentos --inicializar --perfil "$PERFIL"
