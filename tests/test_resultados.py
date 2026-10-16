"""Pruebas de validación, agregación, comparación y CSV. Los datos son SINTÉTICOS (fixtures de prueba)."""
import pytest
from src import analizar_resultados as an
from src import comparar_paper as cp
from src.ejecutar_experimentos import (construir_plan, construir_registro, duracion_estimada_s, puntos_pendientes)
from src.configuracion import cargar_configuracion, perfil
from src.utilidades import CAMPOS_CSV


def fila(**kw):
    base = dict(run_id="t", vector=1, escenario="C", repeticion=1, clientes=2000, duracion_s=120, tps=800.0, p50_ms=100.0,
                p95_ms=2900.0, ok=96000, fallidas=1000, total_solicitudes=97000, tasa_rechazo_pct=100 * 1000 / 97000,
                errores_ejecucion=0, estado="ok", ruta_raw=None, ram_mb_media=1440.0, cs_por_s=10000.0, cpu_sys_pct=14.0,
                perfil="paper", comparable_con_paper="true")
    base.update(kw)
    return base


def test_registro_valido():
    assert an.validar_registro(fila()) == []


@pytest.mark.parametrize("cambio,texto", [
    ({"total_solicitudes": 5}, "total_solicitudes"),
    ({"tasa_rechazo_pct": 150.0}, "fuera de"),
    ({"tasa_rechazo_pct": 10.0}, "inconsistente"),
    ({"tps": 5000.0}, "TPS inconsistente"),
    ({"ok": -1}, "negativo"),
    ({"p95_ms": None}, "falta p95_ms"),
    ({"errores_ejecucion": 5000}, "errores del generador"),
    ({"escenario": ""}, "falta"),
    ({"estado": "rara"}, "estado desconocido"),
])
def test_registro_invalido(cambio, texto):
    assert any(texto in p for p in an.validar_registro(fila(**cambio)))


def test_estados_no_ok_no_se_validan_ni_agregan():
    assert an.agregar([fila(estado="interrumpida"), fila(estado="fallida")]) == []


def test_agregar_estadisticos():
    filas = [fila(repeticion=i, tps=t, ok=int(t * 120), total_solicitudes=int(t * 120) + 1000, fallidas=1000,
                  tasa_rechazo_pct=100 * 1000 / (int(t * 120) + 1000)) for i, t in ((1, 790.0), (2, 800.0), (3, 810.0))]
    ag = an.agregar(filas)
    assert len(ag) == 1 and ag[0]["n"] == 3
    assert ag[0]["tps_media"] == pytest.approx(800.0) and ag[0]["tps_std"] == pytest.approx(10.0)
    assert ag[0]["tps_min"] == 790.0 and ag[0]["tps_max"] == 810.0
    assert ag[0]["p95_ms_agregado"] is None          # sin datos brutos -> N/D, no un promedio inventado
    suma_f, suma_t = 3000, sum(f["total_solicitudes"] for f in filas)
    assert ag[0]["rechazo_pct_agregado"] == pytest.approx(100 * suma_f / suma_t)


def test_n1_sin_dispersion():
    assert an.agregar([fila()])[0]["tps_std"] is None


def test_efectividad_relativa_a_d1():
    ag = [{"escenario": "D1", "tps_media": 10.0, "rechazo_pct_media": 90.0},
          {"escenario": "D3c", "tps_media": 50.0, "rechazo_pct_media": 0.0}]
    e = an.efectividad_defensas(ag)
    assert e == [{"escenario": "D3c", "tps_vs_d1": 5.0, "reduccion_rechazo_pp": 90.0}]


def test_csv_roundtrip_y_sin_sobrescritura(tmp_path):
    ruta = tmp_path / "x.csv"
    an.escribir_fila_csv(ruta, fila(repeticion=1))
    an.escribir_fila_csv(ruta, fila(repeticion=2, p95_ms=None))
    filas = an.leer_csv(ruta)
    assert len(filas) == 2 and list(filas[0].keys()) == CAMPOS_CSV
    assert filas[1]["p95_ms"] is None and filas[0]["tps"] == 800.0   # vacío -> None, no 0


def test_comparacion_con_paper():
    ref = cp.cargar_referencia()
    # valores publicados verificados contra el PDF
    assert ref["vector1"]["A"]["2000"] == {"tps": 29, "p95_ms": 14820.2, "rechazo_pct": 94.8, "ram_mb": 2010, "cs_por_s": 98740, "sys_pct": 91.1}
    assert ref["vector1"]["C"]["2000"]["tps"] == 795 and ref["vector1"]["C"]["2000"]["p95_ms"] == 2890.6
    assert ref["vector2"]["D3c"] == {"tps": 691, "p95_ms": 310.4, "rechazo_pct": 0.0}
    ag = [{"vector": 2, "escenario": "D3b", "clientes": 200, "tps_media": 600.0, "p95_ms_media": 600.0, "rechazo_pct_media": 0.0}]
    r = {x["metrica"]: x for x in cp.comparar(ag, ref, comparable=False)}
    assert r["TPS"]["dif_abs"] == pytest.approx(52.0) and r["TPS"]["dif_pct"] == pytest.approx(100 * 52 / 548)
    assert r["Rechazo (%)"]["dif_pct"] is None and "referencia = 0" in r["Rechazo (%)"]["notas"]
    assert "NO equivalentes" in r["TPS"]["notas"] and "200 clientes" in r["TPS"]["notas"]


def test_punto_sin_referencia_se_omite():
    ag = [{"vector": 1, "escenario": "C", "clientes": 77, "tps_media": 1.0}]
    assert cp.comparar(ag, cp.cargar_referencia(), True) == []


def test_plan_paper_y_reanudacion():
    cfg = cargar_configuracion()
    pf = perfil(cfg, "paper")
    plan = construir_plan(cfg, pf, [1, 2], 3, None, None, False)
    assert len(plan) == 3 * (3 * 6 + 6) == 72
    assert duracion_estimada_s(plan, pf) > 3 * 3600
    hechos = [{"vector": 1, "escenario": "A", "repeticion": 1, "clientes": 50, "estado": "ok"},
              {"vector": 1, "escenario": "A", "repeticion": 1, "clientes": 100, "estado": "interrumpida"}]
    pend = puntos_pendientes(plan, hechos)
    assert len(pend) == 71 and not any(p["escenario"] == "A" and p["clientes"] == 50 and p["repeticion"] == 1 and p["vector"] == 1 for p in pend)


def test_construir_registro_marca_invalida_y_nd():
    cfg = cargar_configuracion(); pf = perfil(cfg, "smoke")
    punto = {"vector": 1, "escenario": "C", "repeticion": 1, "clientes": 50, "atacantes": 0}
    res = {"tps": 10.0, "p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 3.0, "ok": 100, "fallidas": 0, "total_solicitudes": 100,
           "tasa_rechazo_pct": 0.0, "duracion_s": 10, "hilos_lanzados_efectivos": 50}
    meta = {"run_id": "t", "perfil": "smoke", "comparable_con_paper": "false"}
    ok = construir_registro(meta, punto, pf, {**res, "tps": 10.0}, {"motivos": {"cpu": "x"}}, "ok", [], "data/raw/x")
    assert ok["estado"] == "ok" and "N/D cpu" in ok["observaciones"] and ok["ram_mb_media"] is None
    mal = construir_registro(meta, punto, pf, {**res, "hilos_lanzados_efectivos": 30}, None, "ok", [], None)
    assert mal["estado"] == "invalida" and "30/50" in mal["observaciones"]
    inc = construir_registro(meta, punto, pf, {**res, "tps": 99.0}, None, "ok", [], None)
    assert inc["estado"] == "invalida" and "VALIDACION" in inc["observaciones"]
