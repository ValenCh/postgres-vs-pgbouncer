#!/usr/bin/env bash
# Vector 2. Uso: scripts/ejecutar_vector2.sh [perfil] [opciones extra]
set -euo pipefail
cd "$(dirname "$0")/.."
PERFIL="${1:-smoke}"; shift || true
exec python3 -m src.ejecutar_experimentos --vector 2 --perfil "$PERFIL" "$@"
