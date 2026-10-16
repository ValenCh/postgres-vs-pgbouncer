#!/usr/bin/env bash
# Ambos vectores. Uso: scripts/ejecutar_todo.sh [perfil] [opciones extra]
# Con el perfil 'paper' dura ~3,5 h; el programa muestra el plan y pide confirmación.
set -euo pipefail
cd "$(dirname "$0")/.."
PERFIL="${1:-smoke}"; shift || true
exec python3 -m src.ejecutar_experimentos --vector todo --perfil "$PERFIL" "$@"
