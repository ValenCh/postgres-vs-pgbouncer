"""Orquestador: preflight, plan, ejecución de cada punto experimental, registro de resultados.

Uso típico (ver docs/guia_de_ejecucion.md):
    python -m src.ejecutar_experimentos --inicializar --perfil smoke
    python -m src.ejecutar_experimentos --vector 1 --perfil smoke
    python -m src.ejecutar_experimentos --vector todo --perfil paper
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path

from src import analizar_resultados as an
from src import configuracion as cf
from src.recolectar_metricas import Muestreador, calcular_metricas
from src.utilidades import RAIZ, clasificar_mensaje, configurar_logging, fmt

TIEMPO_OVERHEAD_PUNTO_S = 25   # reinicio de PgBouncer, reset de la BD, esperas


# ------------------------------------------------------------------ plan (puro)
def construir_plan(cfg: dict, pf: dict, vectores: list[int], repeticiones: int, niveles: list[int] | None,
                   escenarios: list[str] | None, incluir_extras: bool) -> list[dict]:
    plan = []
    for rep in range(1, repeticiones + 1):
        for vec in vectores:
            for esc in cf.escenarios_para_vector(cfg, vec, incluir_extras):
                if escenarios and esc not in escenarios:
                    continue
                if vec == 1:
                    for n in (niveles or pf["niveles_v1"]):
                        plan.append({"vector": 1, "escenario": esc, "repeticion": rep, "clientes": n, "atacantes": 0})
                else:
                    plan.append({"vector": 2, "escenario": esc, "repeticion": rep,
                                 "clientes": pf["clientes_v2"], "atacantes": pf["atacantes_v2"]})
    return plan


def clave_punto(p: dict) -> tuple:
    return (int(p["vector"]), p["escenario"], int(p["repeticion"]), int(p["clientes"]))


def puntos_pendientes(plan: list[dict], filas_previas: list[dict]) -> list[dict]:
    hechos = {(int(f["vector"]), f["escenario"], int(f["repeticion"]), int(f["clientes"]))
              for f in filas_previas if f.get("estado") == "ok"}
    return [p for p in plan if clave_punto(p) not in hechos]


def duracion_estimada_s(plan: list[dict], pf: dict) -> int:
    return len(plan) * (pf["duracion_s"] + pf["calentamiento_s"] + TIEMPO_OVERHEAD_PUNTO_S)


# ------------------------------------------------------------------ construcción de registro (puro)
def construir_registro(meta: dict, punto: dict, pf: dict, resumen: dict | None, metricas: dict | None,
                       estado: str, obs: list[str], ruta_raw: str | None) -> dict:
    f = {**meta, "vector": punto["vector"], "escenario": punto["escenario"], "repeticion": punto["repeticion"],
         "clientes": punto["clientes"], "atacantes": punto["atacantes"], "duracion_s": pf["duracion_s"],
         "calentamiento_s": pf["calentamiento_s"], "ruta_raw": ruta_raw,
         "fecha_hora_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if resumen:
        for k in ("tps", "p50_ms", "p95_ms", "p99_ms", "p95_con_conexion_ms", "ok", "fallidas", "total_solicitudes"):
            f[k] = resumen.get(k)
        f["tasa_rechazo_pct"] = resumen.get("tasa_rechazo_pct")
        f["errores_ejecucion"] = sum(resumen.get("errores_ejecucion", {}).values())
        if resumen.get("fallos_por_tipo"):
            obs.append("rechazos por tipo: " + ", ".join(f"{k}={v}" for k, v in sorted(resumen["fallos_por_tipo"].items())))
        if resumen.get("errores_ejecucion"):
            obs.append("ERRORES DE EJECUCION (no son rechazos): " + json.dumps(resumen["errores_ejecucion"]) +
                       " ej: " + json.dumps({k: v for k, v in resumen.get("ejemplos_error", {}).items()
                                              if k in resumen["errores_ejecucion"]}, ensure_ascii=False))
        if resumen.get("ataque"):
            obs.append("ataque: " + ", ".join(f"{k}={v}" for k, v in sorted(resumen["ataque"].items())))
        if resumen.get("modo_ataque"):
            obs.append(f"modo_ataque={resumen['modo_ataque']}")
        esperados = punto["clientes"] + punto["atacantes"]
        if resumen.get("hilos_lanzados_efectivos", esperados) < esperados:
            obs.append(f"el generador solo pudo lanzar {resumen['hilos_lanzados_efectivos']}/{esperados} hilos")
            estado = "invalida"
        if resumen.get("ok", 0) == 0:
            obs.append("0 solicitudes exitosas: P95 no disponible (N/D)")
    if metricas:
        for k in ("ram_mb_media", "ram_mb_pico", "ram_pgbouncer_mb", "cpu_sys_pct", "cs_por_s",
                  "conexiones_pg_media", "conexiones_pg_pico", "idle_in_tx_pico"):
            f[k] = metricas.get(k)
        for k, v in metricas.get("motivos", {}).items():
            obs.append(f"N/D {k}: {v}")
    f["estado"] = estado
    f["observaciones"] = " | ".join(obs)
    problemas = an.validar_registro({**f, **{k: an._num(v) if k in an.NUMERICOS else v for k, v in f.items()}}) if estado == "ok" else []
    if problemas:
        f["estado"] = "invalida"
        f["observaciones"] += " | VALIDACION: " + "; ".join(problemas)
    return f


# ------------------------------------------------------------------ docker
class Docker:
    def __init__(self, log):
        self.log = log

    def run(self, cmd: list[str], check=True, timeout=300, input=None) -> subprocess.CompletedProcess:
        self.log.debug("$ %s", " ".join(cmd))
        p = subprocess.run(cmd, cwd=RAIZ, capture_output=True, text=True, timeout=timeout, input=input)
        if check and p.returncode != 0:
            raise RuntimeError(f"Falló '{' '.join(cmd[:4])}...': {(p.stderr or p.stdout).strip()[:400]}")
        return p

    def compose(self, *args, **kw):
        return self.run(["docker", "compose", *args], **kw)

    def psql(self, env: dict, cfg: dict, sql: str, check=True) -> str:
        pr = cfg["proyecto"]
        p = self.compose("exec", "-T", "postgres", "psql", "-U", pr["usuario_admin"], "-d", pr["base_de_datos"],
                         "-Atq", "-c", sql, check=check)
        return p.stdout.strip()

    def info(self) -> dict:
        try:
            p = self.run(["docker", "info", "--format", "{{.NCPU}}|{{.MemTotal}}|{{.OperatingSystem}}|{{.KernelVersion}}|{{.ServerVersion}}"], timeout=30)
            ncpu, mem, so, kern, ver = (p.stdout.strip().split("|") + [""] * 5)[:5]
            return {"ncpu": int(ncpu), "mem_mb": int(mem) // 1048576, "so": so, "kernel": kern, "version": ver}
        except Exception:  # noqa: BLE001
            return {"ncpu": None, "mem_mb": None, "so": "", "kernel": "", "version": ""}

    def esperar_saludable(self, servicio: str, timeout=120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            p = self.compose("ps", "--format", "{{.Service}} {{.Health}} {{.State}}", check=False)
            for linea in p.stdout.splitlines():
                partes = linea.split()
                if partes and partes[0] == servicio and "healthy" in partes[1:2] and "unhealthy" not in partes[1:2]:
                    return
            time.sleep(2)
        raise RuntimeError(f"El servicio '{servicio}' no llegó a estado saludable en {timeout}s "
                           f"(docker compose logs {servicio})")


def puerto_en_uso(puerto: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


# ------------------------------------------------------------------ preflight / inicialización
def verificar_herramientas(dk: Docker):
    for cmd, ayuda in ((["docker", "--version"], "Instale Docker Engine/Desktop"),
                       (["docker", "compose", "version"], "Instale el plugin Docker Compose v2")):
        try:
            dk.run(cmd, timeout=20)
        except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"'{' '.join(cmd)}' no disponible. {ayuda}.") from e


def inicializar(cfg: dict, env: dict, pf: dict, dk: Docker, consola) -> int:
    verificar_herramientas(dk)
    if not (RAIZ / ".env").exists():
        raise RuntimeError("Falta .env. Ejecute: cp .env.example .env  (y cambie las contraseñas)")
    cf.renderizar_runtime(cfg, "C", env)
    activos = dk.compose("ps", "--status", "running", "--services", check=False).stdout.split()
    if "postgres" not in activos:
        for nombre, var, defecto in (("PostgreSQL", "PG_HOST_PORT", 5432), ("PgBouncer", "PGB_HOST_PORT", 6432)):
            pto = int(env.get(var, defecto))
            if puerto_en_uso(pto):
                raise RuntimeError(f"El puerto {pto} ({nombre}) ya está en uso en 127.0.0.1. "
                                   f"Detenga ese servicio o cambie {var} en .env")
    consola.print("[bold]Levantando servicios[/] (la primera vez construye las imágenes)...")
    dk.compose("up", "-d", "--build", timeout=1800)
    dk.esperar_saludable("postgres")
    dk.esperar_saludable("pgbouncer")
    pr = cfg["proyecto"]
    esc = pf["escala"]
    try:
        n = int(dk.psql(env, cfg, "SELECT count(*) FROM pgbench_accounts", check=True) or 0)
    except RuntimeError:
        n = 0
    if n == 100000 * esc:
        consola.print(f"Base ya inicializada con escala {esc} ({n:,} filas). Nada que hacer.")
    else:
        consola.print(f"[bold]Inicializando pgbench -i -s {esc}[/] ({100000 * esc:,} filas; "
                      f"~{'1-3' if esc >= 50 else '<1'} min y ~{esc * 15} MB de disco)...")
        dk.compose("exec", "-T", "postgres", "pgbench", "-i", "-s", str(esc), "-U", pr["usuario_admin"],
                   "-d", pr["base_de_datos"], timeout=3600)
        consola.print("Inicialización completada.")
    consola.print("[green]Entorno listo.[/] Siguiente paso: python -m src.ejecutar_experimentos --vector 1 --perfil " + pf.get("_nombre", "smoke"))
    return 0


def preflight(cfg: dict, env: dict, pf: dict, dk: Docker) -> dict:
    """Valida todo antes de lanzar carga. Devuelve info de entorno."""
    verificar_herramientas(dk)
    if not (RAIZ / ".env").exists():
        raise RuntimeError("Falta .env (cp .env.example .env)")
    cf.credenciales_desde_env(cfg, env)
    activos = dk.compose("ps", "--status", "running", "--services", check=False).stdout.split()
    faltan = [s for s in ("postgres", "pgbouncer", "loadgen") if s not in activos]
    if faltan:
        raise RuntimeError(f"Servicios detenidos: {', '.join(faltan)}. Ejecute scripts/inicializar.sh")
    dk.esperar_saludable("postgres", 30)
    dk.esperar_saludable("pgbouncer", 30)
    esc = pf["escala"]
    try:
        n = int(dk.psql(env, cfg, "SELECT count(*) FROM pgbench_accounts"))
        b = int(dk.psql(env, cfg, "SELECT count(*) FROM pgbench_branches"))
    except (RuntimeError, ValueError) as e:
        raise RuntimeError("La base no está inicializada. Ejecute scripts/inicializar.sh") from e
    if n != 100000 * esc or b != esc:
        raise RuntimeError(f"La base tiene escala {b} pero el perfil requiere {esc}. "
                           f"Reinicialice: python -m src.ejecutar_experimentos --inicializar --perfil <perfil> "
                           f"(borre antes el volumen con scripts/limpiar.sh --volumenes)")
    esperado = cfg["postgres_esperado"]
    for k, v in esperado.items():
        real = dk.psql(env, cfg, f"SHOW {k}")
        if str(real).replace(" ", "").lower() != str(v).replace(" ", "").lower():
            raise RuntimeError(f"PostgreSQL: {k} = {real} pero se esperaba {v}")
    return dk.info()


def versiones(dk: Docker, cfg: dict, env: dict) -> tuple[str, str]:
    try:
        v_pg = dk.psql(env, cfg, "SHOW server_version")
    except RuntimeError:
        v_pg = ""
    try:
        v_pgb = dk.compose("exec", "-T", "pgbouncer", "pgbouncer", "--version", check=False).stdout.splitlines()[0].strip()
    except (IndexError, RuntimeError):
        v_pgb = ""
    return v_pg, v_pgb


# ------------------------------------------------------------------ ejecución de un punto
def preparar_escenario(cfg, env, dk, nombre: str):
    esc = cfg["escenarios"][nombre]
    pr = cfg["proyecto"]
    for rol in (pr["rol_atacante"], pr["rol_legitimo"]):
        dk.psql(env, cfg, f"ALTER ROLE {rol} RESET idle_in_transaction_session_timeout")
    for rol, params in esc.get("pg_roles", {}).items():
        for k, v in params.items():
            dk.psql(env, cfg, f"ALTER ROLE {rol} SET {k} = '{v}'")
    cf.renderizar_runtime(cfg, nombre, env)
    dk.compose("restart", "pgbouncer", timeout=120)
    dk.esperar_saludable("pgbouncer", 60)


def reiniciar_estado_bd(cfg, env, dk):
    pr = cfg["proyecto"]
    dk.psql(env, cfg, "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE backend_type='client backend' "
                      f"AND pid <> pg_backend_pid() AND usename IN ('{pr['rol_legitimo']}','{pr['rol_atacante']}')")
    for _ in range(20):
        n = int(dk.psql(env, cfg, "SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' "
                                  f"AND pid <> pg_backend_pid() AND usename IN ('{pr['rol_legitimo']}','{pr['rol_atacante']}')") or 0)
        if n == 0:
            break
        time.sleep(1)
    dk.psql(env, cfg, "TRUNCATE pgbench_history")
    dk.psql(env, cfg, "CHECKPOINT")


def comando_generador(cfg, pf, punto, salida_cont: str) -> list[str]:
    esc = cfg["escenarios"][punto["escenario"]]
    c, v2 = cfg["carga"], cfg["v2"]
    pausa = c["pausa_ms"] if punto["vector"] == 1 else v2["pausa_legit_ms"]
    cmd = ["docker", "compose", "exec", "-T", "loadgen", "python", "-m", "src.generador_carga",
           "--vector", str(punto["vector"]), "--host", esc["destino_host"], "--puerto", str(esc["destino_puerto"]),
           "--db", cfg["proyecto"]["base_de_datos"], "--clientes", str(punto["clientes"]),
           "--atacantes", str(punto["atacantes"]), "--escala", str(pf["escala"]),
           "--duracion", str(pf["duracion_s"]), "--calentamiento", str(pf["calentamiento_s"]),
           "--ratio-efimeros", str(c["ratio_efimeros"]), "--pausa-ms", str(pausa[0]), str(pausa[1]),
           "--reintento-ms", str(c["reintento_pausa_ms"][0]), str(c["reintento_pausa_ms"][1]),
           "--timeout-conexion", str(c["timeout_conexion_s"]), "--espera-ataque", str(v2["espera_ataque_s"]),
           "--reintento-ataque-ms", str(v2["reintento_ataque_ms"]), "--modo-ataque", v2["modo_ataque"],
           "--rol-legit", cfg["proyecto"]["rol_legitimo"], "--rol-atacante", cfg["proyecto"]["rol_atacante"],
           "--salida", salida_cont]
    return cmd


def ejecutar_punto(cfg, env, pf, dk, punto, meta, run_id, idx, total, consola, log) -> dict:
    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
    esc_nombre = punto["escenario"]
    ruta_rel = f"data/raw/{run_id}/v{punto['vector']}/{esc_nombre}/c{punto['clientes']}_r{punto['repeticion']}"
    obs: list[str] = []
    consola.print(f"\n[bold cyan]Punto {idx}/{total}[/] Vector {punto['vector']} | Escenario {esc_nombre} | "
                  f"{punto['clientes']} clientes" + (f" + {punto['atacantes']} atacantes" if punto["atacantes"] else "") +
                  f" | repetición {punto['repeticion']}/{pf['repeticiones']} | inicio {dt.datetime.now():%H:%M:%S}")
    consola.print(f"  [dim]{cfg['escenarios'][esc_nombre]['nombre']}[/]")
    log.info("INICIO punto %s", punto)
    preparar_escenario(cfg, env, dk, esc_nombre)
    reiniciar_estado_bd(cfg, env, dk)
    muest = Muestreador(cfg["muestreo"]["intervalo_s"], cfg["escenarios"][esc_nombre]["pool_mode"] != "directo", str(RAIZ), log)
    muest.start()
    cmd = comando_generador(cfg, pf, punto, "/app/" + ruta_rel)
    total_s = pf["calentamiento_s"] + pf["duracion_s"]
    proc = subprocess.Popen(cmd, cwd=RAIZ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    t0 = None
    resumen = None
    salida_extra: list[str] = []
    try:
        with Progress(TextColumn("  {task.description}"), BarColumn(), TextColumn("{task.completed:.0f}/{task.total}s"),
                      TimeElapsedColumn(), TextColumn("{task.fields[info]}"), console=consola, transient=True) as pg:
            tarea = pg.add_task("generando carga", total=total_s, info="")
            limite = time.time() + total_s + 180
            for linea in proc.stdout:
                linea = linea.strip()
                campos = linea.split()
                if len(campos) == 5 and campos[0] == "P":
                    try:
                        pg.update(tarea, completed=min(float(campos[1]), total_s), description=f"{campos[2]:13s}",
                                  info=f"ok={campos[3]} rechazos={campos[4]}")
                    except ValueError:
                        log.warning("línea de progreso inválida: %s", linea)
                elif len(campos) == 2 and campos[0] == "T0":
                    try:
                        t0 = float(campos[1])
                    except ValueError:
                        log.warning("T0 inválido: %s", linea)
                elif linea and not linea.startswith(("INICIO", "RESUMEN_JSON")):
                    log.warning("generador: %s", linea)
                    salida_extra.append(linea[:200])
                    if linea.startswith(("ADVERTENCIA", "ERROR")):
                        consola.print(f"  [yellow]{linea}[/]")
                if time.time() > limite:
                    proc.kill()
                    obs.append("generador excedió el tiempo máximo y fue terminado")
                    break
        proc.wait(timeout=60)
    except KeyboardInterrupt:
        consola.print("\n[yellow]Interrupción: deteniendo el generador de forma segura...[/]")
        dk.compose("exec", "-T", "loadgen", "sh", "-c", "kill -TERM $(cat /tmp/generador.pid)", check=False, timeout=30)
        muest.detener()
        reiniciar_estado_bd(cfg, env, dk)
        f = construir_registro(meta, punto, pf, None, None, "interrumpida", ["interrumpida por el usuario"], ruta_rel)
        an.escribir_fila_csv(an.DIR_CSV / f"{run_id}.csv", f)
        raise
    muest.detener()
    ruta_json = RAIZ / ruta_rel / "resumen_carga.json"
    metricas = None
    if ruta_json.exists():
        resumen = json.loads(ruta_json.read_text(encoding="utf-8"))
        if t0 is not None:
            metricas = calcular_metricas(muest.muestras, t0 + pf["calentamiento_s"], t0 + total_s,
                                         float(env.get("PG_CPUS", 4)), muest.muestras_pgb)
        else:
            metricas = {"motivos": {"recursos": "el generador no informó T0"}}
    else:
        obs.append(f"no se generó {ruta_rel}/resumen_carga.json (código de salida {proc.returncode})")
        obs.extend(salida_extra[-5:])
    if salida_extra and resumen:
        obs.append("salida del generador: " + " / ".join(salida_extra[-3:]))
    estado = "ok" if resumen else "fallida"
    if resumen and resumen.get("interrumpido"):
        estado = "interrumpida"
    reg = construir_registro(meta, punto, pf, resumen, metricas, estado, obs, ruta_rel)
    an.escribir_fila_csv(an.DIR_CSV / f"{run_id}.csv", reg)
    reiniciar_estado_bd(cfg, env, dk)       # limpia transacciones/conexiones residuales
    log.info("FIN punto %s estado=%s tps=%s rechazo=%s", punto, reg["estado"], reg.get("tps"), reg.get("tasa_rechazo_pct"))
    color = "green" if reg["estado"] == "ok" else "red"
    consola.print(f"  [{color}]{reg['estado'].upper()}[/] TPS={fmt(reg.get('tps'))}  P95={fmt(reg.get('p95_ms'), 1, ' ms')}  "
                  f"rechazo={fmt(reg.get('tasa_rechazo_pct'), 1, ' %')}  RAM={fmt(reg.get('ram_mb_media'), 0, ' MB')}")
    if reg["estado"] != "ok":
        consola.print(f"  [red]{reg['observaciones'][:300]}[/]")
    return reg


# ------------------------------------------------------------------ main
def main(argv=None) -> int:
    from src.presentacion import aviso_no_comparable, consola, tabla_efectividad, tabla_vector1, tabla_vector2
    ap = argparse.ArgumentParser(description="Ejecuta los vectores experimentales (solo entorno local Docker)")
    ap.add_argument("--vector", choices=("1", "2", "todo"), default="todo")
    ap.add_argument("--perfil", default="smoke", help="smoke | reducido | paper (ver config/experimento.toml)")
    ap.add_argument("--repeticiones", type=int)
    ap.add_argument("--niveles", help="V1: lista de clientes separada por comas, p. ej. 50,100")
    ap.add_argument("--escenarios", help="p. ej. A,C o A,D1,D3c")
    ap.add_argument("--incluir-extras", action="store_true", help="incluye variantes opcionales (D3a_pg)")
    ap.add_argument("--reanudar", metavar="RUN_ID", help="continúa una ejecución interrumpida (omite puntos ya 'ok')")
    ap.add_argument("--si", action="store_true", help="no pedir confirmación")
    ap.add_argument("--solo-plan", action="store_true", help="muestra el plan y los recursos, y termina")
    ap.add_argument("--inicializar", action="store_true", help="levanta Docker e inicializa pgbench -i")
    a = ap.parse_args(argv)

    cfg = cf.cargar_configuracion()
    env = cf.leer_env()
    pf = dict(cf.perfil(cfg, a.perfil))
    pf["_nombre"] = a.perfil
    if a.repeticiones:
        pf["repeticiones"] = a.repeticiones
    niveles = [int(x) for x in a.niveles.split(",")] if a.niveles else None
    escenarios = [x.strip() for x in a.escenarios.split(",")] if a.escenarios else None
    for e in escenarios or []:
        if e not in cfg["escenarios"]:
            print(f"Escenario desconocido '{e}'. Válidos: {', '.join(cfg['escenarios'])}", file=sys.stderr)
            return 2
    vectores = [1, 2] if a.vector == "todo" else [int(a.vector)]
    run_id = a.reanudar or f"{dt.datetime.now():%Y%m%d-%H%M%S}-{a.perfil}"
    log = configurar_logging(RAIZ / "logs" / f"{run_id}.log")
    dk = Docker(log)

    try:
        if a.inicializar:
            return inicializar(cfg, env, pf, dk, consola)
        plan = construir_plan(cfg, pf, vectores, pf["repeticiones"], niveles, escenarios, a.incluir_extras)
        previos = an.cargar_ejecucion(run_id) if a.reanudar else []
        pendientes = puntos_pendientes(plan, previos)
        modificado = bool(a.repeticiones or niveles or escenarios)
        # --- resumen de recursos y plan
        consola.print(f"\n[bold]PLAN DE EJECUCIÓN[/]  run_id={run_id}  perfil={a.perfil}: {pf['descripcion']}")
        consola.print(f"  Puntos: {len(pendientes)} de {len(plan)} ({'reanudando' if a.reanudar else 'nuevo'}) | "
                      f"{pf['duracion_s']} s + {pf['calentamiento_s']} s de calentamiento cada uno | repeticiones: {pf['repeticiones']}")
        h = duracion_estimada_s(pendientes, pf) / 3600
        consola.print(f"  Duración estimada: {h:.1f} h | escala pgbench: {pf['escala']} (~{pf['escala'] * 15} MB de datos)")
        ep = cfg["entorno_paper"]
        consola.print(f"  Recursos requeridos (perfil paper): {ep['vcpu_fisicos_minimos']} vCPU y ~{(ep['pg_ram_mb'] + ep['pgbouncer_ram_mb']) / 1024 + 2:.1f} GB de RAM para Docker")
        if a.solo_plan:
            return 0
        info = preflight(cfg, env, pf, dk)
        so_ef = "linux" if platform.system() == "Linux" and "desktop" not in info["so"].lower() else "Docker Desktop"
        comparable, motivo = cf.comparable_con_paper(a.perfil, info["ncpu"], ep["vcpu_fisicos_minimos"], so_ef)
        if modificado and a.perfil == "paper":
            comparable, motivo = False, "se modificaron repeticiones/niveles/escenarios respecto al diseño del paper"
        consola.print(f"  Docker: {info['ncpu']} CPU, {info['mem_mb']} MB, {info['so']}, kernel {info['kernel']}")
        if not comparable:
            aviso_no_comparable(motivo)
        if not a.si:
            if not sys.stdin.isatty():
                print("Entrada no interactiva: use --si para confirmar.", file=sys.stderr)
                return 2
            if input("¿Iniciar la ejecución? [s/N] ").strip().lower() not in ("s", "si", "sí", "y", "yes"):
                consola.print("Cancelado. No se envió ninguna carga.")
                return 0
        v_pg, v_pgb = versiones(dk, cfg, env)
        recursos = (f"pg {env.get('PG_CPUS', 4)}cpu/{env.get('PG_MEM', '4g')} cpuset {env.get('PG_CPUSET', '0-3')}; "
                    f"pgb {env.get('PGB_CPUS', 1)}cpu/{env.get('PGB_MEM', '512m')}; loadgen {env.get('LG_CPUS', 4)}cpu cpuset {env.get('LG_CPUSET', '5-8')}; "
                    f"docker {info['ncpu']}cpu/{info['mem_mb']}MB")
        meta = {"run_id": run_id, "perfil": a.perfil, "comparable_con_paper": str(comparable).lower(),
                "version_pg": v_pg, "version_pgbouncer": v_pgb,
                "sistema": f"{platform.system()} {platform.release()} / Docker {info['version']} ({info['so']}, kernel {info['kernel']})",
                "recursos": recursos}
        consola.print(f"  Inicio: {dt.datetime.now():%Y-%m-%d %H:%M:%S} | PostgreSQL {v_pg} | {v_pgb}")
        try:
            for i, punto in enumerate(pendientes, 1):
                try:
                    ejecutar_punto(cfg, env, pf, dk, punto, meta, run_id, i, len(pendientes), consola, log)
                except RuntimeError as e:
                    log.error("punto %s: %s", punto, e)
                    consola.print(f"  [red]ERROR en el punto: {e}[/]")
                    f = construir_registro(meta, punto, pf, None, None, "fallida", [str(e)[:300]], None)
                    an.escribir_fila_csv(an.DIR_CSV / f"{run_id}.csv", f)
        except KeyboardInterrupt:
            consola.print(f"[yellow]Ejecución interrumpida. Lo medido hasta ahora está en resultados/csv/{run_id}.csv.[/]\n"
                          f"Para continuar: python -m src.ejecutar_experimentos --reanudar {run_id} --vector {a.vector} --perfil {a.perfil}")
            return 130
        # --- resúmenes por vector
        filas = an.cargar_ejecucion(run_id)
        ag = an.agregar(filas)
        for vec in vectores:
            sub = [x for x in ag if x["vector"] == vec]
            if not sub:
                consola.print(f"[red]Vector {vec}: sin resultados válidos.[/]")
                continue
            if vec == 1:
                tabla_vector1(sub)
            else:
                orden = ["A", "D1", "D2", "D3a", "D3b", "D3c", "D3a_pg"]
                sub.sort(key=lambda x: orden.index(x["escenario"]) if x["escenario"] in orden else 99)
                tabla_vector2(sub)
                tabla_efectividad(an.efectividad_defensas(sub))
        rutas = an.guardar_procesados(run_id, ag)
        malos = [f for f in filas if f.get("estado") != "ok"]
        if malos:
            consola.print(f"[yellow]{len(malos)} registro(s) con estado distinto de 'ok' (ver observaciones en el CSV).[/]")
        consola.print(f"\nResultados guardados en: {an.DIR_CSV / (run_id + '.csv')}\n  Brutos: {RAIZ / 'data' / 'raw' / run_id}\n  Procesados: " +
                      ", ".join(str(r) for r in rutas) + f"\n  Siguiente: python -m src.generar_pdf --run-id {run_id}")
        return 0
    except RuntimeError as e:
        log.error("%s", e)
        consola.print(f"[red]ERROR:[/] {e}")
        return 1
    except FileNotFoundError as e:
        consola.print(f"[red]ERROR:[/] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
