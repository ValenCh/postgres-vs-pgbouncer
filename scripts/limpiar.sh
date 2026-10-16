#!/usr/bin/env bash
# Detiene los contenedores. Opciones:
#   --volumenes   además borra la base de datos (hay que volver a inicializar)
#   --resultados  además borra data/raw, data/processed y resultados/* (pide confirmación)
set -euo pipefail
cd "$(dirname "$0")/.."
VOL=""; RES=0
for a in "$@"; do case "$a" in --volumenes) VOL="-v";; --resultados) RES=1;; *) echo "Opción desconocida: $a"; exit 1;; esac; done
docker compose down $VOL
rm -rf runtime/pgbouncer
if [ "$RES" = 1 ]; then
  read -r -p "Se borrarán TODOS los CSV, datos brutos, gráficos y PDF. ¿Seguro? [s/N] " r
  if [ "$r" = "s" ]; then
    find data/raw data/processed resultados/csv resultados/graficos resultados/pdf -mindepth 1 ! -name .gitkeep -delete
    echo "Resultados borrados."
  fi
fi
echo "Entorno detenido."
