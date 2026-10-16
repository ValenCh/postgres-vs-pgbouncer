"""Utilidades compartidas. SOLO biblioteca estándar: también se ejecuta dentro del
contenedor del generador de carga, que no tiene rich/matplotlib/reportlab."""
from __future__ import annotations

import ipaddress
import logging
import math
import socket
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RUTA_TOML = RAIZ / "config" / "experimento.toml"

# Columnas del CSV de resultados (un registro por punto experimental y repetición).
CAMPOS_CSV = [
    "run_id", "fecha_hora_utc", "perfil", "comparable_con_paper", "vector", "escenario",
    "repeticion", "clientes", "atacantes", "duracion_s", "calentamiento_s",
    "tps", "p50_ms", "p95_ms", "p99_ms", "p95_con_conexion_ms",
    "ok", "fallidas", "total_solicitudes", "tasa_rechazo_pct", "errores_ejecucion",
    "ram_mb_media", "ram_mb_pico", "ram_pgbouncer_mb", "cpu_sys_pct", "cs_por_s",
    "conexiones_pg_media", "conexiones_pg_pico", "idle_in_tx_pico",
    "version_pg", "version_pgbouncer", "sistema", "recursos",
    "estado", "observaciones", "ruta_raw",
]
ESTADOS = ("ok", "invalida", "fallida", "interrumpida")

# Categorías de error cuyo origen es el EXPERIMENTO (rechazos legítimos de medir).
# Las categorías de EXEC_ERRORS son fallos del generador/configuración: NO son rechazos.
EXEC_ERRORS = {"recurso_generador", "autenticacion", "configuracion", "error_interno"}

_PATRONES_ERROR = [  # (subcadena en minúsculas, categoría); el orden importa
    ("too many open files", "recurso_generador"),
    ("cannot assign requested address", "recurso_generador"),
    ("could not create socket", "recurso_generador"),
    ("password authentication failed", "autenticacion"),
    ("no such user", "autenticacion"),
    ("does not exist", "configuracion"),
    ("sorry, too many clients", "too_many_clients"),
    ("remaining connection slots are reserved", "slots_reservados"),
    ("query_wait_timeout", "query_wait_timeout"),
    ("no more connections allowed", "max_client_conn"),
    ("pgbouncer cannot connect to server", "pooler_sin_servidor"),
    ("idle-in-transaction", "idle_in_tx_timeout"),
    ("idle transaction timeout", "idle_in_tx_timeout"),
    ("idle in transaction", "idle_in_tx_timeout"),
    ("user connections", "cuota_rol"),
    ("connection refused", "conexion_rechazada"),
    ("timeout expired", "timeout_conexion"),
    ("timed out", "timeout_conexion"),
    ("server closed the connection", "conexion_cerrada"),
    ("connection already closed", "conexion_cerrada"),
    ("eof detected", "conexion_cerrada"),
    ("terminating connection", "conexion_cerrada"),
    ("connection reset", "conexion_cerrada"),
    ("server conn crashed", "conexion_cerrada"),
    ("closed the connection", "conexion_cerrada"),
]


def configurar_logging(archivo: Path | None = None, nivel=logging.INFO) -> logging.Logger:
    log = logging.getLogger("resiliencia")
    if log.handlers:
        return log
    log.setLevel(nivel)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    if archivo is not None:
        archivo.parent.mkdir(parents=True, exist_ok=True)
        h = logging.FileHandler(archivo, encoding="utf-8")
        h.setFormatter(fmt)
        log.addHandler(h)
    return log


def cargar_toml(ruta: Path | str | None = None) -> dict:
    with open(ruta or RUTA_TOML, "rb") as f:
        return tomllib.load(f)


# ----------------------------------------------------------------- estadística
def percentil(valores: list[float], p: float) -> float | None:
    """Percentil p (0-100) con interpolación lineal entre rangos (igual que
    numpy.percentile(..., method='linear')). Devuelve None si no hay datos:
    NUNCA 0, porque 0 ms sería una medición falsa."""
    if not valores:
        return None
    if not 0 <= p <= 100:
        raise ValueError("p debe estar entre 0 y 100")
    v = sorted(valores)
    if len(v) == 1:
        return float(v[0])
    pos = (len(v) - 1) * p / 100.0
    i = math.floor(pos)
    j = min(i + 1, len(v) - 1)
    return float(v[i] + (v[j] - v[i]) * (pos - i))


def media(valores: list[float]) -> float | None:
    return sum(valores) / len(valores) if valores else None


def desviacion_muestral(valores: list[float]) -> float | None:
    """Desviación estándar muestral (n-1). None si n < 2."""
    n = len(valores)
    if n < 2:
        return None
    m = sum(valores) / n
    return math.sqrt(sum((x - m) ** 2 for x in valores) / (n - 1))


def tasa_rechazo(ok: int, fallidas: int) -> float | None:
    """% de rechazo = fallidas / (ok + fallidas) * 100. Cada intento cuenta UNA vez.
    None si no hubo ningún intento (no se puede definir la tasa)."""
    total = ok + fallidas
    return None if total == 0 else 100.0 * fallidas / total


def diferencia_porcentual(medido: float | None, referencia: float | None) -> float | None:
    """(medido - ref) / ref * 100; None si no es matemáticamente válida (ref = 0 o ausente)."""
    if medido is None or referencia is None or referencia == 0:
        return None
    return 100.0 * (medido - referencia) / referencia


def fmt(valor, decimales: int = 1, sufijo: str = "") -> str:
    """Formato para terminal/PDF. Una métrica ausente se muestra 'N/D', nunca 0."""
    if valor is None or valor == "":
        return "N/D"
    try:
        return f"{float(valor):,.{decimales}f}{sufijo}"
    except (TypeError, ValueError):
        return str(valor)


# ---------------------------------------------------------------------- errores
def clasificar_mensaje(mensaje: str) -> str:
    m = (mensaje or "").lower()
    for patron, categoria in _PATRONES_ERROR:
        if patron in m:
            return categoria
    return "otro"


def clasificar_excepcion(exc: BaseException) -> str:
    """Una excepción que no viene del driver de PostgreSQL es un bug/recurso del
    generador (error de ejecución), no un rechazo del sistema bajo prueba."""
    if not type(exc).__module__.startswith("psycopg2"):
        if isinstance(exc, OSError):
            return clasificar_mensaje(str(exc)) if clasificar_mensaje(str(exc)) != "otro" else "recurso_generador"
        return "error_interno"
    return clasificar_mensaje(str(exc))


def es_error_ejecucion(categoria: str) -> bool:
    return categoria in EXEC_ERRORS


# -------------------------------------------------------------------- seguridad
def validar_host_permitido(host: str, permitidos: list[str], resolver=socket.getaddrinfo) -> None:
    """Aísla el experimento: el host debe estar en la lista blanca Y resolver
    únicamente a direcciones loopback o privadas. Lanza ValueError si no."""
    if host not in permitidos:
        raise ValueError(f"Host '{host}' no permitido. Solo se admite: {', '.join(permitidos)}")
    try:
        infos = resolver(host, None)
    except OSError as e:
        raise ValueError(f"No se pudo resolver '{host}': {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not (ip.is_loopback or ip.is_private):
            raise ValueError(f"'{host}' resuelve a {ip}, que no es una dirección local/privada")
