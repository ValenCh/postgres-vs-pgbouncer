"""Comparación de las mediciones con los valores PUBLICADOS en el paper.
Los valores de referencia viven en config/referencia_paper.json (separados de lo medido).
Una diferencia NO prueba por sí sola que el paper sea incorrecto: el hardware, el kernel,
el generador de carga y a veces el diseño (perfil reducido) son distintos."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from src.utilidades import RAIZ, diferencia_porcentual, fmt

RUTA_REF = RAIZ / "config" / "referencia_paper.json"
# (clave en referencia, clave en agregados, etiqueta, decimales)
METRICAS_V1 = [("tps", "tps_media", "TPS", 1), ("p95_ms", "p95_ms_media", "P95 (ms)", 1),
               ("rechazo_pct", "rechazo_pct_media", "Rechazo (%)", 1), ("ram_mb", "ram_mb_media", "RAM (MB)", 0),
               ("cs_por_s", "cs_por_s_media", "CS/s", 0), ("sys_pct", "cpu_sys_pct_media", "%Sys", 1)]
METRICAS_V2 = METRICAS_V1[:3]
NOTAS_METRICA = {"cs_por_s": "paper: CS/s; aquí se mide el host/VM completo (ver metricas_y_resultados.md)",
                 "ram_mb": "aquí: cgroup del contenedor de PostgreSQL (working set)"}


def cargar_referencia(ruta: Path | None = None) -> dict:
    return json.loads((ruta or RUTA_REF).read_text(encoding="utf-8"))


def comparar(agregados: list[dict], referencia: dict, comparable: bool, clientes_v2_paper: int = 500) -> list[dict]:
    filas = []
    for ag in agregados:
        vec, esc, cli = ag["vector"], ag["escenario"], ag["clientes"]
        if vec == 1:
            ref_pt = referencia["vector1"].get(esc, {}).get(str(cli))
            metricas = METRICAS_V1
        else:
            ref_pt = referencia["vector2"].get(esc)
            metricas = METRICAS_V2
        if not ref_pt:
            continue
        for kref, kag, etiqueta, dec in metricas:
            r, m = ref_pt.get(kref), ag.get(kag)
            notas = []
            if not comparable:
                notas.append("condiciones NO equivalentes al paper")
            if vec == 2 and cli != clientes_v2_paper:
                notas.append(f"{cli} clientes legítimos (paper: {clientes_v2_paper})")
            if kref in NOTAS_METRICA:
                notas.append(NOTAS_METRICA[kref])
            dif = None if (m is None or r is None) else m - r
            dp = diferencia_porcentual(m, r)
            if m is not None and r == 0:
                notas.append("dif. % no definida: referencia = 0")
            if m is None:
                notas.append("N/D: métrica no medida")
            filas.append({"vector": vec, "escenario": esc, "clientes": cli, "metrica": etiqueta, "decimales": dec,
                          "referencia": r, "medido": m, "dif_abs": dif, "dif_pct": dp, "notas": "; ".join(notas)})
    return filas


def escribir_csv(ruta: Path, filas: list[dict]):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    campos = ["vector", "escenario", "clientes", "metrica", "referencia", "medido", "dif_abs", "dif_pct", "notas"]
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        for r in filas:
            w.writerow({k: ("N/D" if r.get(k) is None and k in ("medido", "dif_abs", "dif_pct") else ("" if r.get(k) is None else r.get(k))) for k in campos})


def main(argv=None) -> int:
    from src import analizar_resultados as an
    from src.presentacion import aviso_no_comparable, consola
    from rich.table import Table
    ap = argparse.ArgumentParser(description="Compara una ejecución con los valores del paper")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run-id")
    g.add_argument("--ultima-ejecucion", action="store_true")
    a = ap.parse_args(argv)
    run_id = an.ultima_ejecucion() if a.ultima_ejecucion else a.run_id
    if not run_id:
        print("No hay ejecuciones.", file=sys.stderr)
        return 1
    filas = an.cargar_ejecucion(run_id)
    ag = an.agregar(filas)
    comparable = bool(ag) and str(ag[0].get("comparable_con_paper")).lower() == "true"
    res = comparar(ag, cargar_referencia(), comparable)
    consola.print("[bold]Los valores 'Paper' son los PUBLICADOS; 'Medido' proviene de esta ejecución.[/]")
    if not comparable:
        aviso_no_comparable("La ejecución no cumple las condiciones del paper; las diferencias no invalidan al paper.")
    t = Table(title=f"Comparación con el paper - {run_id}", header_style="bold cyan")
    for c in ("Vec", "Esc", "Clientes", "Métrica", "Paper", "Medido", "Dif.", "Dif. %"):
        t.add_column(c, justify="right" if c not in ("Esc", "Métrica") else "left")
    for r in res:
        d = r["decimales"]
        t.add_row(str(r["vector"]), r["escenario"], str(r["clientes"]), r["metrica"], fmt(r["referencia"], d),
                  fmt(r["medido"], d), fmt(r["dif_abs"], d), fmt(r["dif_pct"], 1, " %"))
    consola.print(t)
    ruta = an.DIR_PROC / run_id / "comparacion_paper.csv"
    escribir_csv(ruta, res)
    consola.print(f"Guardado: {ruta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
