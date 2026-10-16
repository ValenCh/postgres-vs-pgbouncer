"""Informe PDF local (reportlab) a partir de los resultados REALES de una ejecución.
No existe modo demo: sin resultados válidos no se genera informe (no se inventan datos)."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

from src import analizar_resultados as an
from src.comparar_paper import cargar_referencia, comparar
from src.generar_graficos import generar_graficos
from src.utilidades import RAIZ, fmt

EST = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=EST["Heading1"], fontSize=15, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=EST["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
P = ParagraphStyle("p", parent=EST["BodyText"], fontSize=9, leading=12)
PS = ParagraphStyle("ps", parent=P, fontSize=7.5, leading=9)
TITULO = ParagraphStyle("t", parent=EST["Title"], fontSize=18, alignment=TA_CENTER, spaceAfter=8)
AVISO = ParagraphStyle("av", parent=P, backColor=colors.HexColor("#fff3cd"), borderColor=colors.HexColor("#e0a800"),
                       borderWidth=0.8, borderPadding=5, spaceBefore=6, spaceAfter=8)


def _tabla(datos, anchos, cabeceras_n=1):
    celdas = [[Paragraph(str(c), PS) for c in fila] for fila in datos]
    t = Table(celdas, colWidths=anchos, repeatRows=cabeceras_n)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, cabeceras_n - 1), colors.HexColor("#d6eaf8")),
                           ("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("ROWBACKGROUNDS", (0, cabeceras_n), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")])]))
    return t


def _pm(m, s, dec=1):
    """media ± desviación (o solo media si n=1 → sin dispersión)."""
    if m is None:
        return "N/D"
    return f"{fmt(m, dec)} ± {fmt(s, dec)}" if s is not None else f"{fmt(m, dec)} (n=1)"


def conclusiones(ag: list[dict]) -> list[str]:
    """Frases generadas SOLO a partir de los datos medidos (sin afirmar nada que no esté en ellos)."""
    out = []
    v1 = [a for a in ag if a["vector"] == 1]
    if v1:
        mx = max(a["clientes"] for a in v1)
        por = {a["escenario"]: a for a in v1 if a["clientes"] == mx}
        partes = [f"{e}: {fmt(por[e]['tps_media'], 1)} TPS, rechazo {fmt(por[e]['rechazo_pct_media'], 1, ' %')}, "
                  f"P95 {fmt(por[e]['p95_ms_media'], 0, ' ms')}" for e in ("A", "B", "C") if e in por]
        out.append(f"Vector 1, con {mx} clientes (nivel más alto medido) - " + "; ".join(partes) + ".")
        if "A" in por and "C" in por and por["A"]["tps_media"] and por["C"]["tps_media"] is not None:
            out.append(f"En ese nivel, C obtuvo {por['C']['tps_media'] / por['A']['tps_media']:.2f} veces el TPS de A "
                       f"(en esta ejecución, no extrapolable).")
    v2 = {a["escenario"]: a for a in ag if a["vector"] == 2}
    if v2:
        ranking = sorted(v2.items(), key=lambda kv: -(kv[1]["tps_media"] or 0))
        out.append("Vector 2, orden por TPS legítimo: " + " > ".join(f"{e} ({fmt(a['tps_media'], 1)})" for e, a in ranking) + ".")
        if "A" in v2 and "D1" in v2:
            rel = "inferior" if v2["D1"]["tps_media"] < v2["A"]["tps_media"] else "no inferior"
            out.append(f"D1 (pooler de 40 conexiones sin defensas) obtuvo un TPS {rel} al de A en esta ejecución "
                       f"({fmt(v2['D1']['tps_media'], 1)} vs {fmt(v2['A']['tps_media'], 1)}).")
    n = sorted({a["n"] for a in ag})
    out.append(f"Cada punto resume {', '.join(map(str, n))} repetición(es) válida(s); con n=1 no hay estimación de dispersión.")
    return out


def construir_pdf(run_id: str, filas: list[dict], destino: Path, con_referencia=True) -> Path:
    ag = an.agregar(filas)
    if not ag:
        raise ValueError("No hay resultados válidos (estado 'ok') en esta ejecución; no se genera informe.")
    ref = cargar_referencia()
    ok = [f for f in filas if f.get("estado") == "ok"]
    base = ok[0]
    comparable = str(base.get("comparable_con_paper")).lower() == "true"
    graf = generar_graficos(ag, ref if con_referencia else None, RAIZ / "resultados" / "graficos" / run_id)
    doc = SimpleDocTemplate(str(destino), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=1.8 * cm, title=f"Informe {run_id}", author="Proyecto de resiliencia PostgreSQL")
    W = 17 * cm
    h = []
    h.append(Paragraph("Evaluación empírica de la resiliencia de la capa de conexión de PostgreSQL", TITULO))
    h.append(Paragraph("Informe de resultados de una ejecución local (reproducción del paper de Chiappini, Marini, Oyarzo y Sánchez)", P))
    h.append(Spacer(1, 6))
    h.append(_tabla([["Identificador de ejecución", run_id], ["Informe generado", dt.datetime.now().strftime("%Y-%m-%d %H:%M")],
                     ["Perfil", str(base.get("perfil"))], ["Comparable con el paper", "Sí (condiciones del paper)" if comparable else "NO"]],
                    [5 * cm, 12 * cm], 0))
    if base.get("perfil") == "smoke":
        h.append(Paragraph("<b>PRUEBA DE FUNCIONAMIENTO.</b> Este perfil verifica que el sistema opera; sus cifras NO constituyen un experimento "
                           "ni son comparables con el paper.", AVISO))
    elif not comparable:
        h.append(Paragraph("<b>Resultados NO comparables directamente con el paper</b> (perfil, hardware o diseño distintos). "
                           "Las diferencias no prueban que el paper sea incorrecto.", AVISO))
    h.append(Paragraph("1. Metodología", H2))
    h.append(Paragraph("Se contrastan PostgreSQL nativo (A), PgBouncer en modo sesión (B) y en modo transacción (C) bajo una inundación de "
                       "conexiones (Vector 1), y un estudio de ablación (A, D1, D2, D3a, D3b, D3c) bajo un ataque de transacciones "
                       "<i>idle in transaction</i> (Vector 2). La carga es el perfil TPC-B de pgbench generada con un cliente en Python que "
                       "contabiliza cada intento. Tasa de rechazo = fallidas / (exitosas + fallidas); el P95 se mide de BEGIN a COMMIT, sin la "
                       "pausa voluntaria. Se descartan los segundos de calentamiento. Detalle en docs/metodologia_experimental.md.", P))
    h.append(Paragraph("2. Entorno experimental", H2))
    h.append(_tabla([["PostgreSQL", base.get("version_pg") or "N/D"], ["PgBouncer", base.get("version_pgbouncer") or "N/D"],
                     ["Sistema", base.get("sistema") or "N/D"], ["Recursos asignados", base.get("recursos") or "N/D"],
                     ["Duración / calentamiento", f"{base.get('duracion_s')} s / {base.get('calentamiento_s')} s por punto"]],
                    [4 * cm, 13 * cm], 0))
    v1 = [a for a in ag if a["vector"] == 1]
    if v1:
        h.append(Paragraph("3. Resultados del Vector 1 (inundación de conexiones)", H2))
        datos = [["Esc.", "Clientes", "TPS (media ± σ)", "P95 ms (media de ejec.)", "P95 ms (agregado)", "Rechazo %", "RAM MB", "CS/s", "%Sys", "n"]]
        for a in sorted(v1, key=lambda a: (a["escenario"], a["clientes"])):
            datos.append([a["escenario"], a["clientes"], _pm(a["tps_media"], a["tps_std"]), _pm(a["p95_ms_media"], a["p95_ms_std"]),
                          fmt(a["p95_ms_agregado"], 1), _pm(a["rechazo_pct_media"], a["rechazo_pct_std"]), fmt(a["ram_mb_media"], 0),
                          fmt(a["cs_por_s_media"], 0), fmt(a["cpu_sys_pct_media"], 1), a["n"]])
        h.append(KeepTogether([_tabla(datos, [1.0 * cm, 1.4 * cm, 2.7 * cm, 2.5 * cm, 2.1 * cm, 2.5 * cm, 1.3 * cm, 1.5 * cm, 1.4 * cm, 0.6 * cm])]))
        for k in ("v1_tps", "v1_p95", "v1_rechazo"):
            if k in graf:
                h.append(KeepTogether([Spacer(1, 6), Image(str(graf[k]), width=W * 0.78, height=W * 0.78 * 0.613)]))
    v2 = [a for a in ag if a["vector"] == 2]
    if v2:
        h.append(PageBreak())
        h.append(Paragraph("4. Resultados del Vector 2 (transacciones inactivas)", H2))
        orden = ["A", "D1", "D2", "D3a", "D3b", "D3c", "D3a_pg"]
        v2.sort(key=lambda a: orden.index(a["escenario"]) if a["escenario"] in orden else 99)
        datos = [["Esc.", "TPS legítimo", "P95 ms (media de ejec.)", "P95 ms (agregado)", "Rechazo legítimo %", "Conex. PG (pico)", "Idle-in-tx (pico)", "n"]]
        for a in v2:
            datos.append([a["escenario"], _pm(a["tps_media"], a["tps_std"]), _pm(a["p95_ms_media"], a["p95_ms_std"]), fmt(a["p95_ms_agregado"], 1),
                          _pm(a["rechazo_pct_media"], a["rechazo_pct_std"]), fmt(a["conexiones_pg_pico_media"], 0), fmt(a["idle_in_tx_pico_media"], 0), a["n"]])
        h.append(KeepTogether([_tabla(datos, [1.3 * cm, 3 * cm, 3 * cm, 2.4 * cm, 3 * cm, 1.9 * cm, 1.8 * cm, 0.6 * cm])]))
        ef = an.efectividad_defensas(v2)
        if ef:
            datos = [["Esc.", "TPS relativo a D1", "Reducción de rechazo vs D1 (p.p.)"]] + \
                    [[e["escenario"], fmt(e["tps_vs_d1"], 2, " x"), fmt(e["reduccion_rechazo_pp"], 1)] for e in ef]
            h.append(Spacer(1, 6))
            h.append(KeepTogether([Paragraph("Efectividad de cada defensa (relativa a D1 de esta misma ejecución)", P), _tabla(datos, [3 * cm, 5 * cm, 7 * cm])]))
        for k in ("v2_tps", "v2_p95", "v2_rechazo"):
            if k in graf:
                h.append(KeepTogether([Spacer(1, 6), Image(str(graf[k]), width=W * 0.78, height=W * 0.78 * 0.587)]))
    h.append(PageBreak())
    h.append(Paragraph("5. Comparación con el paper", H2))
    comp = comparar(ag, ref, comparable)
    h.append(Paragraph("La columna 'Paper' contiene valores PUBLICADOS; 'Medido' proviene de esta ejecución.", P))
    if comp:
        datos = [["Vec.", "Esc.", "Clientes", "Métrica", "Paper", "Medido", "Dif.", "Dif. %"]]
        for r in comp:
            d = r["decimales"]
            datos.append([r["vector"], r["escenario"], r["clientes"], r["metrica"], fmt(r["referencia"], d), fmt(r["medido"], d), fmt(r["dif_abs"], d), fmt(r["dif_pct"], 1, " %")])
        h.append(Spacer(1, 4))
        h.append(_tabla(datos, [1.1 * cm, 1.4 * cm, 1.8 * cm, 2.6 * cm, 2.4 * cm, 2.4 * cm, 2.4 * cm, 2.4 * cm]))
    else:
        h.append(Paragraph("Ningún punto medido tiene valor de referencia en el paper (niveles o escenarios distintos).", P))
    h.append(Paragraph("6. Limitaciones y observaciones", H2))
    for t in ("Hardware, kernel y generador de carga distintos a los del paper; una diferencia no prueba por sí sola que el paper sea incorrecto.",
              "Cambios de contexto por segundo: se miden sobre el host/VM completo (/proc/stat), no solo sobre PostgreSQL.",
              "El paper no especifica la pausa de los clientes legítimos en el Vector 2, la reacción ante fallos ni el tamaño de pool por rol; "
              "los supuestos adoptados están en docs/limitaciones.md.",
              "Los percentiles no se promedian a ciegas: se informan la media de los P95 por ejecución (como el paper) y el P95 agregado de todas las muestras.",
              "En perfiles con n=1 no se estima dispersión."):
        h.append(Paragraph("- " + t, P))
    obs = sorted({f.get("observaciones") for f in filas if f.get("estado") != "ok" and f.get("observaciones")})
    if obs:
        h.append(Paragraph("Registros excluidos (estado distinto de 'ok'): " + "; ".join(o[:160] for o in obs[:5]), P))
    h.append(Paragraph("7. Conclusiones (basadas únicamente en los datos de esta ejecución)", H2))
    for c in conclusiones(ag):
        h.append(Paragraph("- " + c, P))
    destino.parent.mkdir(parents=True, exist_ok=True)

    def pie(c, d):
        c.saveState()
        c.setFont("Helvetica", 7)
        c.drawString(2 * cm, 1 * cm, f"Ejecución {run_id}" + ("" if comparable else "  |  NO comparable con el paper"))
        c.drawRightString(A4[0] - 2 * cm, 1 * cm, f"Página {d.page}")
        c.restoreState()

    doc.build(h, onFirstPage=pie, onLaterPages=pie)
    return destino


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Genera el informe PDF de una ejecución real")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run-id")
    g.add_argument("--ultima-ejecucion", action="store_true")
    ap.add_argument("--sin-referencia", action="store_true", help="no superponer el paper en los gráficos")
    a = ap.parse_args(argv)
    run_id = an.ultima_ejecucion() if a.ultima_ejecucion else a.run_id
    if not run_id:
        print("No hay ejecuciones en resultados/csv/. Ejecute primero un experimento (no se generan informes de ejemplo).", file=sys.stderr)
        return 1
    try:
        ruta = construir_pdf(run_id, an.cargar_ejecucion(run_id), RAIZ / "resultados" / "pdf" / f"informe_{run_id}.pdf", not a.sin_referencia)
    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"PDF generado: {ruta.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
