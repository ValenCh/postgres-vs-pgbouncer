"""Recolección de métricas de recursos (RAM, %Sys, cambios de contexto, conexiones).

Se leen dentro de los contenedores mediante scripts/sonda_recursos.sh (cgroup del propio
contenedor), por lo que no se depende del sistema operativo del host (Linux/WSL2/PowerShell).

Definiciones:
  RAM            = memory.current - inactive_file del cgroup (equivale a `docker stats`),
                   media de las muestras de la ventana de medición. Pico = máximo.
  %Sys           = delta(system_usec) / (delta_t * vCPU asignadas) * 100, solo del
                   contenedor de PostgreSQL.
  Cambios ctx/s  = delta(ctxt de /proc/stat) / delta_t. LIMITACIÓN: /proc/stat es del
                   host/VM completo (incluye generador y PgBouncer), no solo de PostgreSQL.
  Conexiones     = clientes en pg_stat_activity (backend_type='client backend').
"""
from __future__ import annotations

import subprocess
import threading
import time

from src.utilidades import media


def parsear_sonda(texto: str) -> dict:
    """Convierte la salida clave=valor de la sonda en un dict de floats (ignora líneas ajenas)."""
    d: dict[str, float] = {}
    for linea in texto.splitlines():
        if "=" not in linea:
            continue
        k, v = linea.split("=", 1)
        try:
            d[k.strip()] = float(v.strip())
        except ValueError:
            continue
    return d


def calcular_metricas(muestras: list[dict], t_ini: float, t_fin: float, vcpu: float,
                      muestras_pgb: list[dict] | None = None) -> dict:
    """Calcula métricas sobre las muestras con t_ini <= ts <= t_fin.
    Si faltan datos devuelve None en esa métrica y un motivo en 'motivos' (se mostrará N/D)."""
    ventana = [m for m in muestras if m.get("ts") is not None and t_ini <= m["ts"] <= t_fin]
    res: dict = {"ram_mb_media": None, "ram_mb_pico": None, "cpu_sys_pct": None, "cs_por_s": None,
                 "conexiones_pg_media": None, "conexiones_pg_pico": None, "idle_in_tx_pico": None,
                 "ram_pgbouncer_mb": None, "motivos": {}, "n_muestras": len(ventana)}
    mem = [m["mem_bytes"] / 1048576 for m in ventana if "mem_bytes" in m]
    if mem:
        res["ram_mb_media"], res["ram_mb_pico"] = media(mem), max(mem)
    else:
        res["motivos"]["ram"] = "sin muestras de memoria en la ventana"
    if len(ventana) >= 2:
        a, b = ventana[0], ventana[-1]
        dt = b["ts"] - a["ts"]
        if dt > 0 and "cpu_sys_usec" in a and "cpu_sys_usec" in b and vcpu > 0:
            res["cpu_sys_pct"] = max(0.0, (b["cpu_sys_usec"] - a["cpu_sys_usec"]) / (dt * 1e6 * vcpu) * 100)
        else:
            res["motivos"]["cpu"] = "cgroup sin cpu.stat"
        if dt > 0 and "ctxt_host" in a and "ctxt_host" in b:
            res["cs_por_s"] = (b["ctxt_host"] - a["ctxt_host"]) / dt
        else:
            res["motivos"]["cs"] = "sin /proc/stat"
    else:
        res["motivos"]["cpu"] = res["motivos"]["cs"] = "menos de 2 muestras en la ventana"
    con = [m["conexiones"] for m in ventana if "conexiones" in m]
    if con:
        res["conexiones_pg_media"], res["conexiones_pg_pico"] = media(con), max(con)
    else:
        res["motivos"]["conexiones"] = "sin muestras de pg_stat_activity"
    idle = [m["idle_in_tx"] for m in ventana if "idle_in_tx" in m]
    if idle:
        res["idle_in_tx_pico"] = max(idle)
    if muestras_pgb:
        vp = [m["mem_bytes"] / 1048576 for m in muestras_pgb if "mem_bytes" in m and t_ini <= m.get("ts", 0) <= t_fin]
        res["ram_pgbouncer_mb"] = media(vp)
    return res


class Muestreador(threading.Thread):
    """Hilo que llama periódicamente a la sonda en los contenedores."""

    def __init__(self, intervalo_s: float, usar_pgbouncer: bool, cwd: str, log=None):
        super().__init__(daemon=True)
        self.intervalo, self.usar_pgb, self.cwd, self.log = intervalo_s, usar_pgbouncer, cwd, log
        self.muestras: list[dict] = []
        self.muestras_pgb: list[dict] = []
        self.errores = 0
        self._stop_ev = threading.Event()

    def _leer(self, servicio: str, arg: str | None):
        cmd = ["docker", "compose", "exec", "-T", servicio, "sh", "/sonda/sonda_recursos.sh"] + ([arg] if arg else [])
        try:
            p = subprocess.run(cmd, cwd=self.cwd, capture_output=True, text=True, timeout=20)
            if p.returncode == 0:
                return parsear_sonda(p.stdout)
            if self.log:
                self.log.warning("sonda %s falló: %s", servicio, p.stderr.strip()[:200])
        except (subprocess.TimeoutExpired, OSError) as e:
            if self.log:
                self.log.warning("sonda %s error: %s", servicio, e)
        self.errores += 1
        return None

    def run(self):
        while not self._stop_ev.is_set():
            m = self._leer("postgres", "pg")
            if m:
                self.muestras.append(m)
            if self.usar_pgb:
                mp = self._leer("pgbouncer", None)
                if mp:
                    self.muestras_pgb.append(mp)
            self._stop_ev.wait(self.intervalo)

    def detener(self):
        self._stop_ev.set()
        self.join(timeout=25)
