#!/usr/bin/env bash
# Vector 1. Uso: scripts/ejecutar_vector1.sh [perfil] [opciones extra de ejecutar_experimentos]
set -euo pipefail
cd "$(dirname "$0")/.."
PERFIL="${1:-smoke}"; shift || true
exec python3 -m src.ejecutar_experimentos --vector 1 --perfil "$PERFIL" "$@"
