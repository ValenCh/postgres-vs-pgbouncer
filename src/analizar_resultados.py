"""Lectura, validación y agregación de los CSV de resultados.

Estadísticos (por vector/escenario/clientes, sobre las repeticiones con estado 'ok'):
  * TPS, rechazo, RAM, CS/s, %Sys: media, desviación muestral (n-1), mínimo, máximo y CV %.
  * P95 de latencia: NO se promedia a ciegas. Se informan dos cosas distintas:
      - p95_ms_media: media de los P95 de cada ejecución (lo que reporta el paper;
        sirve para comparar con él).
      - p95_ms_agregado: P95 de TODAS las latencias de las repeticiones juntas (se lee de
        los archivos brutos). Es el estadístico correcto para "el P95 del punto".
  * Rechazo agregado = suma(fallidas) / suma(ok + fallidas) de las repeticiones.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import sys
from collections import defaultdict
from pathlib import Path

from src.utilidades import (CAMPOS_CSV, ESTADOS, RAIZ, desviacion_muestral, media, percentil, tasa_rechazo)

DIR_CSV = RAIZ / "resultados" / "csv"
DIR_PROC = RAIZ / "data" / "processed"
NUMERICOS = {"repeticion", "clientes", "atacantes", "duracion_s", "calentamiento_s", "tps", "p50_ms", "p95_ms",
             "p99_ms", "p95_con_conexion_ms", "ok", "fallidas", "total_solicitudes", "tasa_rechazo_pct",
             "errores_ejecucion", "ram_mb_media", "ram_mb_pico", "ram_pgbouncer_mb", "cpu_sys_pct", "cs_por_s",
             "conexiones_pg_media", "conexiones_pg_pico", "idle_in_tx_pico", "vector"}
OBLIGATORIOS = ("run_id", "vector", "escenario", "repeticion", "clientes", "duracion_s", "estado")


def _num(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() and "." not in str(v) else f
    except ValueError:
        return None


def leer_csv(ruta: Path) -> list[dict]:
    with open(ruta, newline="", encoding="utf-8") as f:
        filas = []
        for r in csv.DictReader(f):
            for k in NUMERICOS:
                if k in r:
                    r[k] = _num(r[k])
            filas.append(r)
    return filas


def escribir_fila_csv(ruta: Path, fila: dict):
    """Añade una fila (y cabecera si el archivo es nuevo). Cada punto se persiste de inmediato."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not ruta.exists() or ruta.stat().st_size == 0
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_CSV, extrasaction="ignore")
        if nuevo:
            w.writeheader()
        w.writerow({k: ("" if fila.get(k) is None else fila.get(k)) for k in CAMPOS_CSV})


def validar_registro(f: dict) -> list[str]:
    """Devuelve la lista de problemas (vacía = registro válido)."""
    p = []
    for c in OBLIGATORIOS:
        if f.get(c) in (None, ""):
            p.append(f"falta '{c}'")
    if p:
        return p
    if f["estado"] not in ESTADOS:
        p.append(f"estado desconocido '{f['estado']}'")
    if f["estado"] != "ok":
        return p
    for c in ("ok", "fallidas", "total_solicitudes", "tps", "tasa_rechazo_pct"):
        if f.get(c) is None:
            p.append(f"falta '{c}'")
        elif f[c] < 0:
            p.append(f"'{c}' negativo")
    if p:
        return p
    if f["ok"] + f["fallidas"] != f["total_solicitudes"]:
        p.append("ok + fallidas != total_solicitudes")
    if not 0 <= f["tasa_rechazo_pct"] <= 100:
        p.append("tasa de rechazo fuera de [0, 100]")
    elif f["total_solicitudes"] and abs(f["tasa_rechazo_pct"] - tasa_rechazo(f["ok"], f["fallidas"])) > 0.01:
        p.append("tasa de rechazo inconsistente con fallidas/total")
    if f["duracion_s"] and abs(f["tps"] - f["ok"] / f["duracion_s"]) > 0.01 * max(1.0, f["tps"]):
        p.append("TPS inconsistente con ok/duración")
    if f["ok"] > 0:
        if f.get("p95_ms") is None:
            p.append("hay solicitudes exitosas pero falta p95_ms")
        elif f.get("p50_ms") is not None and not f["p50_ms"] <= f["p95_ms"] + 1e-9:
            p.append("p50 > p95")
    if f.get("errores_ejecucion") and f["total_solicitudes"] + f["errores_ejecucion"] > 0 \
            and f["errores_ejecucion"] / (f["total_solicitudes"] + f["errores_ejecucion"]) > 0.01:
        p.append("más del 1 % de los intentos fueron errores del generador (no del sistema bajo prueba)")
    return p


def ultima_ejecucion() -> str | None:
    archivos = sorted(DIR_CSV.glob("*.csv"), key=lambda p: p.stat().st_mtime)
    return archivos[-1].stem if archivos else None


def _latencias_brutas(ruta_raw: str | None) -> list[float] | None:
    if not ruta_raw:
        return None
    arch = Path(ruta_raw) if Path(ruta_raw).is_absolute() else RAIZ / ruta_raw
    arch = arch / "latencias.csv.gz"
    if not arch.exists():
        return None
    with gzip.open(arch, "rt", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return [float(x["lat_ms"]) for x in r]


def _estad(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return dict(media=None, std=None, min=None, max=None, cv=None)
    m = media(v)
    s = desviacion_muestral(v)
    return dict(media=m, std=s, min=min(v), max=max(v), cv=(100 * s / m if s is not None and m else None))


def agregar(filas: list[dict]) -> list[dict]:
    grupos = defaultdict(list)
    for f in filas:
        if f.get("estado") == "ok" and not validar_registro(f):
            grupos[(f["vector"], f["escenario"], f["clientes"])].append(f)
    salida = []
    for (vec, esc, cli), fs in sorted(grupos.items()):
        d = {"vector": vec, "escenario": esc, "clientes": cli, "n": len(fs),
             "perfil": fs[0].get("perfil"), "comparable_con_paper": fs[0].get("comparable_con_paper")}
        for campo, clave in (("tps", "tps"), ("p95_ms", "p95_ms"), ("p50_ms", "p50_ms"), ("rechazo_pct", "tasa_rechazo_pct"),
                             ("ram_mb", "ram_mb_media"), ("cs_por_s", "cs_por_s"), ("cpu_sys_pct", "cpu_sys_pct"),
                             ("conexiones_pg_pico", "conexiones_pg_pico"), ("idle_in_tx_pico", "idle_in_tx_pico")):
            e = _estad([f.get(clave) for f in fs])
            d[f"{campo}_media"], d[f"{campo}_std"] = e["media"], e["std"]
            d[f"{campo}_min"], d[f"{campo}_max"], d[f"{campo}_cv_pct"] = e["min"], e["max"], e["cv"]
        d["rechazo_pct_agregado"] = tasa_rechazo(sum(f["ok"] for f in fs), sum(f["fallidas"] for f in fs))
        brutas = [_latencias_brutas(f.get("ruta_raw")) for f in fs]
        if all(b is not None for b in brutas) and any(brutas):
            d["p95_ms_agregado"] = percentil([x for b in brutas for x in b], 95)
        else:
            d["p95_ms_agregado"] = None   # N/D: faltan los datos brutos
        salida.append(d)
    return salida


def efectividad_defensas(agregados_v2: list[dict]) -> list[dict]:
    """Relativa a D1 DE LA MISMA EJECUCIÓN: TPS/TPS_D1 y puntos porcentuales de rechazo menos."""
    por = {a["escenario"]: a for a in agregados_v2}
    d1 = por.get("D1")
    out = []
    for esc, a in por.items():
        if esc == "D1" or d1 is None:
            continue
        r = {"escenario": esc, "tps_vs_d1": None, "reduccion_rechazo_pp": None}
        if d1["tps_media"]:
            r["tps_vs_d1"] = a["tps_media"] / d1["tps_media"]
        if a["rechazo_pct_media"] is not None and d1["rechazo_pct_media"] is not None:
            r["reduccion_rechazo_pp"] = d1["rechazo_pct_media"] - a["rechazo_pct_media"]
        out.append(r)
    return out


def guardar_procesados(run_id: str, agregados: list[dict]) -> list[Path]:
    destino = DIR_PROC / run_id
    destino.mkdir(parents=True, exist_ok=True)
    rutas = []
    for vec in (1, 2):
        fs = [a for a in agregados if a["vector"] == vec]
        if not fs:
            continue
        ruta = destino / f"resumen_vector{vec}.csv"
        campos = list(fs[0].keys())
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            w.writeheader()
            for a in fs:
                w.writerow({k: ("" if v is None else v) for k, v in a.items()})
        rutas.append(ruta)
    return rutas


def cargar_ejecucion(run_id: str) -> list[dict]:
    ruta = DIR_CSV / f"{run_id}.csv"
    if not ruta.exists():
        raise FileNotFoundError(f"No existe {ruta}")
    return leer_csv(ruta)


def main(argv=None) -> int:
    from src.presentacion import consola, tabla_efectividad, tabla_vector1, tabla_vector2
    ap = argparse.ArgumentParser(description="Analiza los resultados de una ejecución")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run-id")
    g.add_argument("--ultima-ejecucion", action="store_true")
    a = ap.parse_args(argv)
    run_id = ultima_ejecucion() if a.ultima_ejecucion else a.run_id
    if not run_id:
        print("No hay ejecuciones en resultados/csv/. Ejecute primero un experimento.", file=sys.stderr)
        return 1
    filas = cargar_ejecucion(run_id)
    invalidas = [(f, validar_registro(f)) for f in filas if validar_registro(f) or f.get("estado") != "ok"]
    for f, pr in invalidas:
        consola.print(f"[yellow]Registro excluido[/] {f.get('escenario')} c={f.get('clientes')} r={f.get('repeticion')} "
                      f"estado={f.get('estado')}: {'; '.join(pr) or f.get('observaciones') or ''}")
    ag = agregar(filas)
    rutas = guardar_procesados(run_id, ag)
    consola.print(f"[bold]Ejecución {run_id}[/]  ({len(filas)} registros, {len(invalidas)} excluidos)")
    if any(x["vector"] == 1 for x in ag):
        tabla_vector1([x for x in ag if x["vector"] == 1])
    v2 = [x for x in ag if x["vector"] == 2]
    if v2:
        tabla_vector2(v2)
        tabla_efectividad(efectividad_defensas(v2))
    for r in rutas:
        consola.print(f"Guardado: {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
