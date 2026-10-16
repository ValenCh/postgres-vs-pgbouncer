"""Gráficos (matplotlib, backend sin pantalla). Solo grafica datos medidos; la referencia
del paper se superpone en línea punteada y rotulada como 'Paper (referencia)'."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from src.utilidades import RAIZ  # noqa: E402

COLORES = {"A": "#c0392b", "B": "#e67e22", "C": "#2471a3", "D1": "#7f8c8d", "D2": "#b7950b",
           "D3a": "#1e8449", "D3b": "#8e44ad", "D3c": "#2471a3", "D3a_pg": "#148f77"}
NOMBRES = {"A": "A (nativo)", "B": "B (sesión)", "C": "C (transacción)", "D1": "D1 (sin defensa)",
           "D2": "D2 (PG 5000 ms)", "D3a": "D3a (500 ms)", "D3b": "D3b (cuota 15)", "D3c": "D3c (500 ms + cuota)",
           "D3a_pg": "D3a-PG (variante)"}


def _serie_v1(ag, esc, clave):
    pts = sorted([a for a in ag if a["vector"] == 1 and a["escenario"] == esc], key=lambda a: a["clientes"])
    return ([p["clientes"] for p in pts], [p.get(clave + "_media") for p in pts], [p.get(clave + "_std") for p in pts])


def _lineas_v1(ag, ref, clave, ref_clave, titulo, ylabel, destino: Path, log_y=False):
    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    hay = False
    for esc in ("A", "B", "C"):
        x, y, s = _serie_v1(ag, esc, clave)
        validos = [(a, b, c) for a, b, c in zip(x, y, s) if b is not None]
        if not validos:
            continue
        hay = True
        xs, ys, ss = zip(*validos)
        ax.errorbar(xs, ys, yerr=[v or 0 for v in ss], marker="o", color=COLORES[esc], capsize=3, label=f"{NOMBRES[esc]} - medido")
        if ref:
            rx = [c for c in xs if str(c) in ref["vector1"].get(esc, {})]
            if rx:
                ax.plot(rx, [ref["vector1"][esc][str(c)][ref_clave] for c in rx], linestyle="--", marker="x",
                        color=COLORES[esc], alpha=0.55, label=f"{NOMBRES[esc]} - Paper (referencia)")
    if not hay:
        plt.close(fig)
        return None
    ax.set_title(titulo)
    ax.set_xlabel("Clientes concurrentes (clientes)")
    ax.set_ylabel(ylabel)
    ax.set_xscale("log")
    ax.set_xticks(sorted({a["clientes"] for a in ag if a["vector"] == 1}))
    ax.get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
    if log_y:
        ax.set_yscale("log")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(destino, dpi=150)
    plt.close(fig)
    return destino


def _barras_v2(ag, ref, clave, ref_clave, titulo, ylabel, destino: Path, log_y=False):
    orden = [e for e in ("A", "D1", "D2", "D3a", "D3b", "D3c", "D3a_pg") if any(a["escenario"] == e and a["vector"] == 2 for a in ag)]
    if not orden:
        return None
    por = {a["escenario"]: a for a in ag if a["vector"] == 2}
    vals = [por[e].get(clave + "_media") for e in orden]
    errs = [por[e].get(clave + "_std") or 0 for e in orden]
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    xs = range(len(orden))
    ax.bar(xs, [v if v is not None else 0 for v in vals], yerr=errs, capsize=3,
           color=[COLORES[e] for e in orden], label="Medido")
    for i, v in enumerate(vals):
        if v is None:
            ax.text(i, 0, "N/D", ha="center", va="bottom", fontsize=8)
    if ref:
        rx = [i for i, e in enumerate(orden) if e in ref["vector2"]]
        ax.plot(rx, [ref["vector2"][orden[i]][ref_clave] for i in rx], linestyle="none", marker="D", color="black",
                label="Paper (referencia)")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([NOMBRES[e] for e in orden], rotation=20, ha="right", fontsize=8)
    ax.set_title(titulo)
    ax.set_ylabel(ylabel)
    if log_y:
        ax.set_yscale("log")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(destino, dpi=150)
    plt.close(fig)
    return destino


def generar_graficos(agregados: list[dict], referencia: dict | None, destino: Path) -> dict[str, Path]:
    destino.mkdir(parents=True, exist_ok=True)
    g = {}
    g["v1_tps"] = _lineas_v1(agregados, referencia, "tps", "tps", "Vector 1: rendimiento (TPS) vs. concurrencia", "Rendimiento (TPS, transacciones/s)", destino / "v1_tps.png")
    g["v1_p95"] = _lineas_v1(agregados, referencia, "p95_ms", "p95_ms", "Vector 1: latencia P95 vs. concurrencia", "Latencia P95 (ms, escala log)", destino / "v1_p95.png", log_y=True)
    g["v1_rechazo"] = _lineas_v1(agregados, referencia, "rechazo_pct", "rechazo_pct", "Vector 1: tasa de rechazo vs. concurrencia", "Tasa de rechazo (% de solicitudes)", destino / "v1_rechazo.png")
    g["v2_tps"] = _barras_v2(agregados, referencia, "tps", "tps", "Vector 2: TPS de la carga legítima", "Rendimiento (TPS, transacciones/s)", destino / "v2_tps.png")
    g["v2_p95"] = _barras_v2(agregados, referencia, "p95_ms", "p95_ms", "Vector 2: latencia P95 de la carga legítima", "Latencia P95 (ms, escala log)", destino / "v2_p95.png", log_y=True)
    g["v2_rechazo"] = _barras_v2(agregados, referencia, "rechazo_pct", "rechazo_pct", "Vector 2: rechazo de solicitudes legítimas", "Tasa de rechazo (% de solicitudes)", destino / "v2_rechazo.png")
    return {k: v for k, v in g.items() if v}


def main(argv=None) -> int:
    from src import analizar_resultados as an
    from src.comparar_paper import cargar_referencia
    ap = argparse.ArgumentParser(description="Genera los gráficos de una ejecución")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run-id")
    g.add_argument("--ultima-ejecucion", action="store_true")
    ap.add_argument("--sin-referencia", action="store_true")
    a = ap.parse_args(argv)
    run_id = an.ultima_ejecucion() if a.ultima_ejecucion else a.run_id
    if not run_id:
        print("No hay ejecuciones.", file=sys.stderr)
        return 1
    ag = an.agregar(an.cargar_ejecucion(run_id))
    if not ag:
        print("Sin resultados válidos para graficar.", file=sys.stderr)
        return 1
    rutas = generar_graficos(ag, None if a.sin_referencia else cargar_referencia(), RAIZ / "resultados" / "graficos" / run_id)
    for k, v in rutas.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
