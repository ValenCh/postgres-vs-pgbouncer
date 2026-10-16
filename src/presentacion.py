"""Salida por terminal (rich). Solo presentación: no calcula nada."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.utilidades import fmt

consola = Console()


def aviso_no_comparable(motivo: str):
    consola.print(Panel(f"[bold yellow]Resultados NO comparables directamente con el paper[/]\n{motivo}",
                        border_style="yellow"))


def tabla_vector1(filas: list[dict], titulo="VECTOR 1 - RESUMEN EXPERIMENTAL"):
    t = Table(title=titulo, header_style="bold cyan")
    for c in ("Esc.", "Clientes", "TPS", "P95", "Rechazo", "RAM", "CS/s", "%Sys", "Reps"):
        t.add_column(c, justify="left" if c in ("Escenario", "Esc.") else "right")
    for f in sorted(filas, key=lambda x: (x["escenario"], x["clientes"])):
        t.add_row(f["escenario"], str(f["clientes"]), fmt(f.get("tps_media"), 1),
                  fmt(f.get("p95_ms_media"), 1, " ms"), fmt(f.get("rechazo_pct_media"), 1, " %"),
                  fmt(f.get("ram_mb_media"), 0, " MB"), fmt(f.get("cs_por_s_media"), 0),
                  fmt(f.get("cpu_sys_pct_media"), 1, " %"), str(f.get("n", "N/D")))
    consola.print(t)


def tabla_vector2(filas: list[dict], titulo="VECTOR 2 - RESUMEN EXPERIMENTAL (A, D1, D2, D3a, D3b, D3c)"):
    t = Table(title=titulo, header_style="bold cyan")
    for c in ("Esc.", "TPS leg.", "P95", "Rechazo", "Conex. pico",
              "Idle-tx pico", "RAM", "Reps"):
        t.add_column(c, justify="left" if c in ("Escenario", "Esc.") else "right")
    for f in filas:
        t.add_row(f["escenario"], fmt(f.get("tps_media"), 1), fmt(f.get("p95_ms_media"), 1, " ms"),
                  fmt(f.get("rechazo_pct_media"), 1, " %"), fmt(f.get("conexiones_pg_pico_media"), 0),
                  fmt(f.get("idle_in_tx_pico_media"), 0), fmt(f.get("ram_mb_media"), 0, " MB"), str(f.get("n", "N/D")))
    consola.print(t)


def tabla_efectividad(filas: list[dict]):
    t = Table(title="EFECTIVIDAD DE LAS DEFENSAS (relativa a D1, misma ejecución)", header_style="bold cyan")
    for c in ("Escenario", "TPS vs D1", "Rechazo menos (p.p.)"):
        t.add_column(c, justify="left" if c in ("Escenario", "Esc.") else "right")
    for f in filas:
        t.add_row(f["escenario"], fmt(f.get("tps_vs_d1"), 2, " x"), fmt(f.get("reduccion_rechazo_pp"), 1))
    consola.print(t)
