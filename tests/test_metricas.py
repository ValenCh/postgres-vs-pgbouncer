"""Pruebas de las funciones de cálculo con datos SINTÉTICOS de valor conocido (no son resultados experimentales)."""
import pytest
from src.recolectar_metricas import calcular_metricas, parsear_sonda
from src.utilidades import (clasificar_excepcion, clasificar_mensaje, desviacion_muestral, diferencia_porcentual,
                            es_error_ejecucion, fmt, percentil, tasa_rechazo)


def test_percentil_interpolacion_lineal():
    assert percentil(list(range(1, 101)), 95) == pytest.approx(95.05)
    assert percentil([10], 95) == 10
    assert percentil([1, 2, 3, 4], 50) == 2.5
    assert percentil([], 95) is None          # nunca 0


def test_percentil_rango_invalido():
    with pytest.raises(ValueError):
        percentil([1], 101)


def test_tasa_rechazo_denominador():
    assert tasa_rechazo(95, 5) == 5.0
    assert tasa_rechazo(0, 10) == 100.0
    assert tasa_rechazo(0, 0) is None         # sin intentos: indefinida, no 0 %


def test_desviacion_muestral():
    assert desviacion_muestral([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.13809, rel=1e-4)
    assert desviacion_muestral([5]) is None


def test_diferencia_porcentual():
    assert diferencia_porcentual(110, 100) == pytest.approx(10.0)
    assert diferencia_porcentual(5, 0) is None
    assert diferencia_porcentual(None, 100) is None


def test_fmt_nd_nunca_cero():
    assert fmt(None) == "N/D" and fmt("") == "N/D"
    assert fmt(0.0, 1, " %") == "0.0 %"       # un cero REAL sí se muestra
    assert fmt(1234.5, 0) == "1,234" or fmt(1234.5, 0) == "1,235"


@pytest.mark.parametrize("msg,cat", [
    ("FATAL:  sorry, too many clients already", "too_many_clients"),
    ("FATAL:  remaining connection slots are reserved for roles with the SUPERUSER attribute", "slots_reservados"),
    ("FATAL:  query_wait_timeout", "query_wait_timeout"),
    ("FATAL:  no more connections allowed (max_client_conn)", "max_client_conn"),
    ("terminating connection due to idle-in-transaction timeout", "idle_in_tx_timeout"),
    ("server closed the connection unexpectedly", "conexion_cerrada"),
    ("[Errno 24] Too many open files", "recurso_generador"),
    ("password authentication failed for user", "autenticacion"),
    ("algo desconocido", "otro"),
])
def test_clasificar_mensaje(msg, cat):
    assert clasificar_mensaje(msg) == cat


def test_errores_de_ejecucion_no_son_rechazos():
    assert es_error_ejecucion("recurso_generador") and es_error_ejecucion("autenticacion")
    assert not es_error_ejecucion("too_many_clients") and not es_error_ejecucion("query_wait_timeout")
    assert clasificar_excepcion(KeyError("x")) == "error_interno"
    assert clasificar_excepcion(OSError(24, "Too many open files")) == "recurso_generador"


def test_parsear_sonda_ignora_basura():
    d = parsear_sonda("ts=100.5\nmem_bytes=1048576\nruido sin igual\nconexiones=7\nmalo=abc\n")
    assert d == {"ts": 100.5, "mem_bytes": 1048576.0, "conexiones": 7.0}


def _muestras():
    return [{"ts": t, "mem_bytes": (1000 + 10 * t) * 1048576, "cpu_sys_usec": t * 1e6 * 0.5 * 4,
             "ctxt_host": t * 1000, "conexiones": 10 + t, "idle_in_tx": t} for t in (0, 5, 10)]


def test_calcular_metricas_valores_conocidos():
    m = calcular_metricas(_muestras(), 0, 10, vcpu=4)
    assert m["ram_mb_media"] == pytest.approx(1050) and m["ram_mb_pico"] == pytest.approx(1100)
    assert m["cpu_sys_pct"] == pytest.approx(50.0)          # 0.5 s de sys por s sobre 1 s*... = 2 cpu-s/s de 4 => 50 %
    assert m["cs_por_s"] == pytest.approx(1000)
    assert m["conexiones_pg_pico"] == 20 and m["idle_in_tx_pico"] == 10


def test_calcular_metricas_ventana_y_nd():
    m = calcular_metricas(_muestras(), 4, 6, vcpu=4)       # una sola muestra en la ventana
    assert m["cpu_sys_pct"] is None and m["cs_por_s"] is None and "cpu" in m["motivos"]
    vacio = calcular_metricas([], 0, 10, 4)
    assert vacio["ram_mb_media"] is None and vacio["conexiones_pg_media"] is None
