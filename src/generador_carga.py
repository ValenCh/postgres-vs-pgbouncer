"""Generador de carga complementario a pgbench (se ejecuta dentro del contenedor `loadgen`).

Por qué no se usa pgbench para la carga: pgbench aborta el hilo cliente cuando una
conexión falla o el servidor la cierra, y no cuenta intentos fallidos. El paper define
el rechazo por PETICIÓN (timeout de cola o error de conexión) y mide el P95 excluyendo la
pausa Z, así que hace falta un generador que contabilice cada intento. pgbench sí se usa
para inicializar la base (pgbench -i -s N).

Unidad de medida: un INTENTO = [conectar si hace falta] + BEGIN..COMMIT del perfil TPC-B
(3 UPDATE, 1 SELECT, 1 INSERT). Un intento exitoso aporta 1 latencia; uno fallido suma 1
rechazo (una sola vez). Solo se cuentan intentos que TERMINAN dentro de la ventana de
medición (tras el calentamiento).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import random
import select
import signal
import sys
import threading
import time
from array import array
from collections import Counter
from pathlib import Path

from src.utilidades import (cargar_toml, clasificar_excepcion, es_error_ejecucion, percentil,
                            tasa_rechazo, validar_host_permitido)

PIDFILE = "/tmp/generador.pid"


class Contexto:
    def __init__(self, a):
        self.a = a
        self.t0 = time.time()
        self.t_ini_med = self.t0 + a.calentamiento
        self.t_fin = self.t_ini_med + a.duracion
        self.stop = threading.Event()
        self.interrumpido = False
        self.passwords = {a.rol_legit: os.environ.get("APP_CORE_PASSWORD", ""),
                          a.rol_atacante: os.environ.get("APP_PORTAL_PASSWORD", "")}


class Registro:
    """Buffer por hilo (sin locks)."""
    def __init__(self, grupo: str):
        self.grupo = grupo
        self.ok: list[tuple[float, float, float]] = []   # (t_rel, lat_ms, lat_total_ms)
        self.t_fallos = array("d")
        self.fallos = Counter()
        self.exec_err = Counter()
        self.muestras: dict[str, str] = {}
        self.n_ok = 0
        self.n_fail = 0
        self.ataque = Counter()


def _conectar(ctx: Contexto, usuario: str):
    import psycopg2
    a = ctx.a
    conn = psycopg2.connect(host=a.host, port=a.puerto, dbname=a.db, user=usuario,
                            password=ctx.passwords[usuario], connect_timeout=a.timeout_conexion,
                            application_name="generador_carga")
    conn.autocommit = True
    return conn


def _cerrar(conn):
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass


def _tpcb(cur, escala: int, rnd: random.Random) -> float:
    """Transacción TPC-B de pgbench. Latencia: primera sentencia (BEGIN) -> COMMIT confirmado."""
    aid = rnd.randint(1, 100000 * escala)
    tid = rnd.randint(1, 10 * escala)
    bid = rnd.randint(1, escala)
    d = rnd.randint(-5000, 5000)
    t0 = time.perf_counter()
    cur.execute("BEGIN")
    cur.execute("UPDATE pgbench_accounts SET abalance = abalance + %s WHERE aid = %s", (d, aid))
    cur.execute("SELECT abalance FROM pgbench_accounts WHERE aid = %s", (aid,))
    cur.fetchone()
    cur.execute("UPDATE pgbench_tellers SET tbalance = tbalance + %s WHERE tid = %s", (d, tid))
    cur.execute("UPDATE pgbench_branches SET bbalance = bbalance + %s WHERE bid = %s", (d, bid))
    cur.execute("INSERT INTO pgbench_history (tid, bid, aid, delta, mtime) "
                "VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)", (tid, bid, aid, d))
    cur.execute("COMMIT")
    return (time.perf_counter() - t0) * 1000.0


def _ok(ctx: Contexto, reg: Registro, lat: float, lat_total: float):
    ahora = time.time()
    if ctx.t_ini_med <= ahora < ctx.t_fin:
        reg.ok.append((ahora - ctx.t_ini_med, lat, lat_total))
        reg.n_ok += 1


def _fallo(ctx: Contexto, reg: Registro, exc: BaseException):
    ahora = time.time()
    if not (ctx.t_ini_med <= ahora < ctx.t_fin):
        return
    cat = clasificar_excepcion(exc)
    reg.muestras.setdefault(cat, str(exc).strip().splitlines()[0][:200] if str(exc).strip() else type(exc).__name__)
    if es_error_ejecucion(cat):
        reg.exec_err[cat] += 1       # error del generador: NO es rechazo, NO entra en el denominador
        return
    reg.fallos[cat] += 1
    reg.t_fallos.append(ahora - ctx.t_ini_med)
    reg.n_fail += 1


def _pausa(ctx: Contexto, rnd: random.Random, rango_ms):
    ctx.stop.wait(rnd.uniform(rango_ms[0], rango_ms[1]) / 1000.0)


def cliente_efimero(ctx: Contexto, reg: Registro, rnd: random.Random):
    """Equivale a pgbench -C: conexión nueva por transacción, sin pausa voluntaria."""
    a = ctx.a
    while not ctx.stop.is_set():
        conn = None
        t_ini = time.perf_counter()
        try:
            conn = _conectar(ctx, a.rol_legit)
            lat = _tpcb(conn.cursor(), a.escala, rnd)
            _ok(ctx, reg, lat, (time.perf_counter() - t_ini) * 1000.0)
        except Exception as e:  # noqa: BLE001 - se clasifica
            _fallo(ctx, reg, e)
            _pausa(ctx, rnd, a.reintento_ms)
        finally:
            _cerrar(conn)


def cliente_persistente(ctx: Contexto, reg: Registro, rnd: random.Random):
    """Conexión persistente con pausa uniforme (\\sleep) FUERA del bloque BEGIN..COMMIT."""
    a = ctx.a
    conn = None
    while not ctx.stop.is_set():
        t_ini = time.perf_counter()
        try:
            if conn is None:
                conn = _conectar(ctx, a.rol_legit)
            lat = _tpcb(conn.cursor(), a.escala, rnd)
            _ok(ctx, reg, lat, (time.perf_counter() - t_ini) * 1000.0)
        except Exception as e:  # noqa: BLE001
            _fallo(ctx, reg, e)
            _cerrar(conn)
            conn = None
            _pausa(ctx, rnd, a.reintento_ms)
            continue
        _pausa(ctx, rnd, a.pausa_ms)
    _cerrar(conn)


def _esperar_atacante(ctx: Contexto, conn) -> str:
    a = ctx.a
    if a.modo_ataque == "sleep_literal":
        ctx.stop.wait(a.espera_ataque)
        return "espera_completa"
    fin = time.perf_counter() + a.espera_ataque
    while time.perf_counter() < fin and not ctx.stop.is_set():
        try:
            legible, _, _ = select.select([conn.fileno()], [], [], 0.05)
        except (OSError, ValueError):
            return "cerrada_por_servidor"
        if legible:                      # el servidor/pooler cerró o envió un FATAL
            return "cerrada_por_servidor"
    return "espera_completa"


def atacante(ctx: Contexto, reg: Registro, rnd: random.Random):
    """Ataque idle-in-transaction (Sección 3.3): BEGIN + SELECT y se detiene SIN COMMIT."""
    a = ctx.a
    while not ctx.stop.is_set():
        conn = None
        try:
            conn = _conectar(ctx, a.rol_atacante)
            conn.cursor().execute("BEGIN; SELECT abalance FROM pgbench_accounts WHERE aid = 1;")
            reg.ataque["transacciones_retenidas"] += 1
            reg.ataque[_esperar_atacante(ctx, conn)] += 1
        except Exception as e:  # noqa: BLE001
            reg.ataque["fallos:" + clasificar_excepcion(e)] += 1
            ctx.stop.wait(a.reintento_ataque_ms / 1000.0)
        finally:
            _cerrar(conn)      # cerrar descarta (ROLLBACK) la transacción retenida


def _progreso(ctx: Contexto, registros: list[Registro]):
    while not ctx.stop.is_set():
        t = time.time() - ctx.t0
        fase = "calentamiento" if time.time() < ctx.t_ini_med else "medicion"
        ok = sum(r.n_ok for r in registros)
        fl = sum(r.n_fail for r in registros)
        print(f"P {t:.0f} {fase} {ok} {fl}", flush=True)
        ctx.stop.wait(2.0)


def _resumir(ctx: Contexto, registros: list[Registro]) -> dict:
    a = ctx.a
    legit = [r for r in registros if r.grupo != "atacante"]
    lats = [x[1] for r in legit for x in r.ok]
    lats_tot = [x[2] for r in legit for x in r.ok]
    ok = sum(r.n_ok for r in legit)
    fl = sum(r.n_fail for r in legit)
    fallos = Counter()
    exec_err = Counter()
    muestras = {}
    for r in legit:
        fallos.update(r.fallos)
        exec_err.update(r.exec_err)
        for k, v in r.muestras.items():
            muestras.setdefault(k, v)
    ataque = Counter()
    for r in registros:
        ataque.update(r.ataque)
    por_grupo = {}
    for g in ("efimero", "persistente"):
        rg = [r for r in legit if r.grupo == g]
        if rg:
            lg = [x[1] for r in rg for x in r.ok]
            okg, flg = sum(r.n_ok for r in rg), sum(r.n_fail for r in rg)
            por_grupo[g] = {"clientes": len(rg), "ok": okg, "fallidas": flg,
                            "tps": okg / a.duracion, "p95_ms": percentil(lg, 95),
                            "tasa_rechazo_pct": tasa_rechazo(okg, flg)}
    return {
        "vector": a.vector, "duracion_s": a.duracion, "calentamiento_s": a.calentamiento,
        "clientes": a.clientes, "atacantes": a.atacantes if a.vector == 2 else 0,
        "ok": ok, "fallidas": fl, "total_solicitudes": ok + fl,
        "tps": ok / a.duracion,
        "p50_ms": percentil(lats, 50), "p95_ms": percentil(lats, 95), "p99_ms": percentil(lats, 99),
        "p95_con_conexion_ms": percentil(lats_tot, 95),
        "tasa_rechazo_pct": tasa_rechazo(ok, fl),
        "fallos_por_tipo": dict(fallos), "errores_ejecucion": dict(exec_err),
        "ejemplos_error": muestras, "por_grupo": por_grupo, "ataque": dict(ataque),
        "interrumpido": ctx.interrumpido, "modo_ataque": a.modo_ataque if a.vector == 2 else None,
        "hilos_lanzados": len(registros),
    }


def _guardar(ctx: Contexto, registros: list[Registro], resumen: dict, salida: Path):
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "resumen_carga.json").write_text(json.dumps(resumen, indent=1, ensure_ascii=False), encoding="utf-8")
    with gzip.open(salida / "latencias.csv.gz", "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["grupo", "t_rel_s", "lat_ms", "lat_con_conexion_ms"])
        for r in registros:
            if r.grupo == "atacante":
                continue
            for t, l, lt in r.ok:
                w.writerow([r.grupo, f"{t:.3f}", f"{l:.3f}", f"{lt:.3f}"])
    ok_s = Counter(int(t) for r in registros if r.grupo != "atacante" for t, _, _ in r.ok)
    fl_s = Counter(int(t) for r in registros if r.grupo != "atacante" for t in r.t_fallos)
    with open(salida / "series_por_segundo.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["segundo", "ok", "fallidas"])
        for s in range(int(ctx.a.duracion)):
            w.writerow([s, ok_s.get(s, 0), fl_s.get(s, 0)])


def construir_parser():
    ap = argparse.ArgumentParser(description="Generador de carga del proyecto (solo entorno local)")
    ap.add_argument("--vector", type=int, choices=(1, 2), required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--puerto", type=int, required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--clientes", type=int, required=True, help="V1: total C; V2: clientes legítimos")
    ap.add_argument("--atacantes", type=int, default=0)
    ap.add_argument("--escala", type=int, required=True)
    ap.add_argument("--duracion", type=int, required=True)
    ap.add_argument("--calentamiento", type=int, required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--ratio-efimeros", type=float, default=0.3)
    ap.add_argument("--pausa-ms", type=float, nargs=2, default=(50, 500))
    ap.add_argument("--reintento-ms", type=float, nargs=2, default=(50, 500))
    ap.add_argument("--timeout-conexion", type=int, default=40)
    ap.add_argument("--espera-ataque", type=float, default=10.0)
    ap.add_argument("--reintento-ataque-ms", type=float, default=100.0)
    ap.add_argument("--modo-ataque", choices=("detectar_cierre", "sleep_literal"), default="detectar_cierre")
    ap.add_argument("--rol-legit", default="app_core")
    ap.add_argument("--rol-atacante", default="app_portal")
    ap.add_argument("--semilla", type=int, default=None)
    ap.add_argument("--hosts-permitidos", nargs="*", default=None,
                    help="Solo para pruebas; por defecto se lee config/experimento.toml")
    return ap


def main(argv=None) -> int:
    a = construir_parser().parse_args(argv)
    permitidos = a.hosts_permitidos or cargar_toml()["seguridad"]["hosts_permitidos"]
    try:
        validar_host_permitido(a.host, permitidos)
    except ValueError as e:
        print(f"ERROR DE SEGURIDAD: {e}", file=sys.stderr)
        return 3
    import threading as _t
    _t.stack_size(512 * 1024)
    ctx = Contexto(a)
    try:
        Path(PIDFILE).write_text(str(os.getpid()))
    except OSError:
        pass

    def _term(*_):
        ctx.interrumpido = True
        ctx.stop.set()
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    base = random.Random(a.semilla)
    registros: list[Registro] = []
    hilos: list[threading.Thread] = []

    def lanzar(fn, grupo):
        reg = Registro(grupo)
        registros.append(reg)
        h = threading.Thread(target=fn, args=(ctx, reg, random.Random(base.random())), daemon=True)
        hilos.append(h)

    if a.vector == 1:
        n_ef = round(a.clientes * a.ratio_efimeros)
        for _ in range(n_ef):
            lanzar(cliente_efimero, "efimero")
        for _ in range(a.clientes - n_ef):
            lanzar(cliente_persistente, "persistente")
    else:
        for _ in range(a.clientes):
            lanzar(cliente_persistente, "persistente")
        for _ in range(a.atacantes):
            lanzar(atacante, "atacante")

    print(f"INICIO vector={a.vector} hilos={len(hilos)} host={a.host}:{a.puerto}", flush=True)
    threading.Thread(target=_progreso, args=(ctx, registros), daemon=True).start()
    ctx.t0 = time.time()
    ctx.t_ini_med = ctx.t0 + a.calentamiento
    ctx.t_fin = ctx.t_ini_med + a.duracion
    print(f"T0 {ctx.t0:.3f}", flush=True)   # el orquestador alinea la ventana de métricas de recursos
    try:
        for h in hilos:
            try:
                h.start()
            except RuntimeError as e:   # no se pudo crear el hilo: error del generador
                print(f"ADVERTENCIA: no se pudo crear un hilo: {e}", file=sys.stderr, flush=True)
                break
        while time.time() < ctx.t_fin and not ctx.stop.is_set():
            time.sleep(0.2)
    finally:
        ctx.stop.set()
        time.sleep(0.5)

    resumen = _resumir(ctx, registros)
    resumen["hilos_vivos_al_cierre"] = sum(h.is_alive() for h in hilos)
    resumen["hilos_lanzados_efectivos"] = sum(1 for h in hilos if h.ident is not None)
    _guardar(ctx, registros, resumen, Path(a.salida))
    print(f"RESUMEN_JSON {Path(a.salida) / 'resumen_carga.json'}", flush=True)
    sys.stdout.flush()
    os._exit(0 if not ctx.interrumpido else 130)   # los hilos bloqueados en red son daemon


if __name__ == "__main__":
    sys.exit(main())
