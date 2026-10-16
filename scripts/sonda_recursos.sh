#!/bin/sh
# Se ejecuta DENTRO de un contenedor (docker compose exec). Imprime clave=valor.
# Uso: sonda_recursos.sh [pg]   ("pg" añade conexiones desde pg_stat_activity)
# Lee el cgroup del propio contenedor, por lo que funciona igual en Linux nativo,
# Docker Desktop (WSL2) y macOS. cgroup v2 con respaldo a v1.
echo "ts=$(date +%s.%N)"
if [ -r /sys/fs/cgroup/cpu.stat ]; then
  cur=$(cat /sys/fs/cgroup/memory.current 2>/dev/null)
  inact=$(awk '$1=="inactive_file"{print $2}' /sys/fs/cgroup/memory.stat 2>/dev/null)
  echo "mem_bytes=$((cur - ${inact:-0}))"
  echo "cpu_sys_usec=$(awk '$1=="system_usec"{print $2}' /sys/fs/cgroup/cpu.stat)"
  echo "cpu_usage_usec=$(awk '$1=="usage_usec"{print $2}' /sys/fs/cgroup/cpu.stat)"
elif [ -r /sys/fs/cgroup/memory/memory.usage_in_bytes ]; then
  cur=$(cat /sys/fs/cgroup/memory/memory.usage_in_bytes)
  inact=$(awk '$1=="total_inactive_file"{print $2}' /sys/fs/cgroup/memory/memory.stat 2>/dev/null)
  echo "mem_bytes=$((cur - ${inact:-0}))"
  # cpuacct.stat está en ticks de USER_HZ (100/s) -> microsegundos
  echo "cpu_sys_usec=$(awk '$1=="system"{print $2*10000}' /sys/fs/cgroup/cpuacct/cpuacct.stat)"
  echo "cpu_usage_usec=$(awk '{s+=$2*10000} END{print s}' /sys/fs/cgroup/cpuacct/cpuacct.stat)"
fi
# Cambios de contexto: /proc/stat NO está aislado por contenedor -> es del host/VM completa.
echo "ctxt_host=$(awk '$1=="ctxt"{print $2}' /proc/stat)"
echo "procesos=$(ls -d /proc/[0-9]* 2>/dev/null | wc -l)"
if [ "$1" = "pg" ]; then
  psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" -Atq -c \
   "SELECT format(E'conexiones=%s\nidle_in_tx=%s', count(*), count(*) FILTER (WHERE state='idle in transaction')) FROM pg_stat_activity WHERE backend_type='client backend' AND pid <> pg_backend_pid()" 2>/dev/null
fi
