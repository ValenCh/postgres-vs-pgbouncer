"""Carga y validación de la configuración; generación de los archivos de runtime
de PgBouncer. Separado de la ejecución para poder probarlo sin Docker."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from string import Template

from src.utilidades import RAIZ, cargar_toml

RUNTIME_PGB = RAIZ / "runtime" / "pgbouncer"
MODOS_VALIDOS = {"directo", "session", "transaction"}
PARAMS_PG_ROL_PERMITIDOS = {"idle_in_transaction_session_timeout"}


def cargar_configuracion(ruta=None) -> dict:
    cfg = cargar_toml(ruta)
    problemas = validar_configuracion(cfg)
    if problemas:
        raise ValueError("Configuración inválida:\n - " + "\n - ".join(problemas))
    return cfg


def perfil(cfg: dict, nombre: str) -> dict:
    if nombre not in cfg["perfiles"]:
        raise KeyError(f"Perfil '{nombre}' inexistente. Disponibles: {', '.join(cfg['perfiles'])}")
    return cfg["perfiles"][nombre]


def escenarios_para_vector(cfg: dict, vector: int, incluir_extras: bool = False) -> list[str]:
    nombres = []
    for nombre, esc in cfg["escenarios"].items():
        if vector in esc["vectores"] and (incluir_extras or not esc.get("extra", False)):
            nombres.append(nombre)
    return nombres


def validar_configuracion(cfg: dict) -> list[str]:
    p: list[str] = []
    for nombre, pf in cfg.get("perfiles", {}).items():
        for k in ("escala", "duracion_s", "calentamiento_s", "repeticiones", "clientes_v2", "atacantes_v2"):
            if not isinstance(pf.get(k), int) or pf[k] <= 0:
                p.append(f"perfiles.{nombre}.{k} debe ser un entero > 0")
        if not pf.get("niveles_v1") or any((not isinstance(n, int) or n <= 0) for n in pf["niveles_v1"]):
            p.append(f"perfiles.{nombre}.niveles_v1 debe ser una lista de enteros > 0")
    carga = cfg.get("carga", {})
    if not 0 <= carga.get("ratio_efimeros", -1) <= 1:
        p.append("carga.ratio_efimeros debe estar entre 0 y 1")
    for clave in ("pausa_ms", "reintento_pausa_ms"):
        v = carga.get(clave, [])
        if len(v) != 2 or v[0] < 0 or v[0] > v[1]:
            p.append(f"carga.{clave} debe ser [min, max] con 0 <= min <= max")
    if cfg.get("v2", {}).get("modo_ataque") not in ("detectar_cierre", "sleep_literal"):
        p.append("v2.modo_ataque debe ser 'detectar_cierre' o 'sleep_literal'")
    permitidos = cfg.get("seguridad", {}).get("hosts_permitidos", [])
    if not permitidos:
        p.append("seguridad.hosts_permitidos no puede estar vacío")
    for nombre, esc in cfg.get("escenarios", {}).items():
        if esc.get("pool_mode") not in MODOS_VALIDOS:
            p.append(f"escenarios.{nombre}.pool_mode inválido: {esc.get('pool_mode')}")
        if esc.get("destino_host") not in permitidos:
            p.append(f"escenarios.{nombre}.destino_host '{esc.get('destino_host')}' no está en hosts_permitidos")
        if esc.get("pool_mode") != "directo" and not esc.get("max_db_connections"):
            p.append(f"escenarios.{nombre} requiere max_db_connections")
        for rol, params in esc.get("pg_roles", {}).items():
            for k in params:
                if k not in PARAMS_PG_ROL_PERMITIDOS:
                    p.append(f"escenarios.{nombre}.pg_roles.{rol}.{k}: parámetro de PostgreSQL no admitido")
        if "idle_transaction_timeout" in esc or "idle_transaction_timeout_ms" in esc:
            p.append(f"escenarios.{nombre}: use idle_transaction_timeout_s (PgBouncer, en segundos)")
    return p


# ------------------------------------------------------------------ PgBouncer
def generar_pgbouncer_ini(cfg: dict, escenario: str, *, pg_host: str = "postgres", pg_port: int = 5432,
                          listen_addr: str = "0.0.0.0", listen_port: int = 6432,
                          auth_file: str = "/etc/pgbouncer/userlist.txt") -> str:
    esc = cfg["escenarios"][escenario]
    g = cfg["pgbouncer"]
    # Escenario A no usa PgBouncer: se genera una configuración inocua (modo transaction, 40).
    modo = esc["pool_mode"] if esc["pool_mode"] != "directo" else "transaction"
    extra = []
    if "idle_transaction_timeout_s" in esc:
        # Parámetro NATIVO de PgBouncer (segundos, admite fracciones; verificado en 1.22.0).
        extra.append(f"idle_transaction_timeout = {esc['idle_transaction_timeout_s']}")
    users = [f"{rol} = max_user_connections={n}" for rol, n in esc.get("cuota_por_rol", {}).items()]
    plantilla = Template((RAIZ / "config" / "pgbouncer" / "pgbouncer.ini.template").read_text(encoding="utf-8"))
    return plantilla.substitute(
        escenario=escenario, nombre=esc["nombre"], db=cfg["proyecto"]["base_de_datos"],
        pg_host=pg_host, pg_port=pg_port, max_db_connections=esc.get("max_db_connections", 40),
        users_lines="\n".join(users), listen_addr=listen_addr, listen_port=listen_port,
        auth_type=g["auth_type"], auth_file=auth_file, admin=cfg["proyecto"]["usuario_admin"],
        pool_mode=modo, max_client_conn=g["max_client_conn"], default_pool_size=g["default_pool_size"],
        min_pool_size=g["min_pool_size"], query_wait_timeout=g["query_wait_timeout_s"],
        extra="\n".join(extra),
    )


def generar_userlist(credenciales: dict[str, str]) -> str:
    return "".join(f'"{u}" "{p}"\n' for u, p in credenciales.items())


def leer_env(ruta: Path | None = None) -> dict[str, str]:
    ruta = ruta or (RAIZ / ".env")
    datos: dict[str, str] = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                datos[k.strip()] = v.strip().strip('"').strip("'")
    return datos


def credenciales_desde_env(cfg: dict, env: dict[str, str]) -> dict[str, str]:
    pr = cfg["proyecto"]
    mapa = {pr["usuario_admin"]: "POSTGRES_PASSWORD", pr["rol_legitimo"]: "APP_CORE_PASSWORD",
            pr["rol_atacante"]: "APP_PORTAL_PASSWORD"}
    faltan = [v for v in mapa.values() if not env.get(v)]
    if faltan:
        raise ValueError(f"Faltan variables en .env: {', '.join(faltan)} (copie .env.example a .env)")
    return {u: env[v] for u, v in mapa.items()}


def renderizar_runtime(cfg: dict, escenario: str, env: dict[str, str]) -> Path:
    """Escribe runtime/pgbouncer/{pgbouncer.ini,userlist.txt}. Devuelve el directorio."""
    RUNTIME_PGB.mkdir(parents=True, exist_ok=True)
    ini = RUNTIME_PGB / "pgbouncer.ini"
    ini.write_text(generar_pgbouncer_ini(cfg, escenario), encoding="utf-8")
    ul = RUNTIME_PGB / "userlist.txt"
    ul.write_text(generar_userlist(credenciales_desde_env(cfg, env)), encoding="utf-8")
    os.chmod(ini, 0o644)
    os.chmod(ul, 0o644)  # lo lee el usuario 'pgbouncer' del contenedor; credenciales locales de laboratorio
    return RUNTIME_PGB


def comparable_con_paper(perfil_nombre: str, ncpu_docker: int | None, minimos: int, so_docker: str | None) -> tuple[bool, str]:
    """Una ejecución solo se etiqueta como comparable con el paper si cumple TODO lo siguiente.
    Aun así, NO es una réplica exacta (hardware, kernel y generador distintos)."""
    if perfil_nombre != "paper":
        return False, f"perfil '{perfil_nombre}' reduce el diseño experimental"
    if ncpu_docker is None or ncpu_docker < minimos:
        return False, f"Docker dispone de {ncpu_docker} CPU; el paper separó {minimos} con cpuset disjuntos"
    if so_docker and so_docker.lower() != "linux":
        return False, f"sistema {so_docker}: sin cpuset/cgroups nativos equivalentes"
    return True, "parámetros del paper; hardware/kernel/generador pueden diferir"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validar configuración o renderizar PgBouncer")
    ap.add_argument("--validar", action="store_true")
    ap.add_argument("--renderizar", metavar="ESCENARIO", help="genera runtime/pgbouncer/* para ese escenario")
    a = ap.parse_args(argv)
    try:
        cfg = cargar_configuracion()
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    if a.validar:
        print("Configuración válida. Escenarios:", ", ".join(cfg["escenarios"]))
    if a.renderizar:
        env = leer_env()
        try:
            d = renderizar_runtime(cfg, a.renderizar, env)
        except (ValueError, KeyError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        print(f"Generado en {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
