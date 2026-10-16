"""Pruebas de configuración: no requieren Docker ni PostgreSQL."""
import re
import pytest
from src import configuracion as cf
from src.utilidades import RAIZ, validar_host_permitido

CFG = cf.cargar_configuracion()


def test_perfil_paper_coincide_con_el_paper():
    p = CFG["perfiles"]["paper"]
    assert p["niveles_v1"] == [50, 100, 250, 500, 1000, 2000]
    assert (p["duracion_s"], p["calentamiento_s"], p["repeticiones"], p["escala"]) == (120, 30, 3, 50)
    assert (p["clientes_v2"], p["atacantes_v2"]) == (500, 60)
    assert CFG["carga"]["ratio_efimeros"] == 0.3 and CFG["carga"]["pausa_ms"] == [50, 500]


def test_escenarios_por_vector():
    assert cf.escenarios_para_vector(CFG, 1) == ["A", "B", "C"]
    assert cf.escenarios_para_vector(CFG, 2) == ["A", "D1", "D2", "D3a", "D3b", "D3c"]
    assert "D3a_pg" in cf.escenarios_para_vector(CFG, 2, incluir_extras=True)


def test_postgresql_conf_coincide_con_toml():
    conf = (RAIZ / "config/postgres/postgresql.conf").read_text()
    def val(k):
        return re.search(rf"^{k}\s*=\s*([^\s#]+)", conf, re.M).group(1)
    e = CFG["postgres_esperado"]
    assert int(val("max_connections")) == e["max_connections"]
    assert int(val("superuser_reserved_connections")) == e["superuser_reserved_connections"]
    assert val("shared_buffers") == "1024MB" and val("work_mem") == e["work_mem"]
    assert val("max_wal_size") == e["max_wal_size"] and val("checkpoint_completion_target") == e["checkpoint_completion_target"]


@pytest.mark.parametrize("esc,modo,maxdb", [("B", "session", 80), ("C", "transaction", 40), ("D1", "transaction", 40)])
def test_ini_modo_y_limite(esc, modo, maxdb):
    ini = cf.generar_pgbouncer_ini(CFG, esc)
    assert f"pool_mode = {modo}" in ini and f"max_db_connections={maxdb}" in ini
    assert "max_client_conn = 2500" in ini and "default_pool_size = 40" in ini
    assert "idle_transaction_timeout" not in ini and "max_user_connections" not in ini


def test_ini_defensas_vector2():
    assert "idle_transaction_timeout = 0.5" in cf.generar_pgbouncer_ini(CFG, "D3a")
    d3b = cf.generar_pgbouncer_ini(CFG, "D3b")
    assert "app_portal = max_user_connections=15" in d3b and "idle_transaction_timeout" not in d3b
    d3c = cf.generar_pgbouncer_ini(CFG, "D3c")
    assert "app_portal = max_user_connections=15" in d3c and "idle_transaction_timeout = 0.5" in d3c


def test_d2_usa_parametro_valido_de_postgresql_16():
    # idle_transaction_timeout NO existe en PostgreSQL 16 (solo en PgBouncer / PG17+)
    assert CFG["escenarios"]["D2"]["pg_roles"]["app_portal"] == {"idle_in_transaction_session_timeout": "5000ms"}
    assert "idle_transaction_timeout" not in str(CFG["escenarios"]["D2"]["pg_roles"])


def test_configuracion_invalida_se_detecta():
    import copy
    malo = copy.deepcopy(CFG)
    malo["escenarios"]["D2"]["pg_roles"]["app_portal"] = {"idle_transaction_timeout": "5000ms"}
    malo["escenarios"]["C"]["destino_host"] = "example.com"
    malo["carga"]["ratio_efimeros"] = 1.5
    p = cf.validar_configuracion(malo)
    assert any("no admitido" in x for x in p) and any("hosts_permitidos" in x for x in p) and any("ratio" in x for x in p)


def test_host_externo_rechazado():
    permitidos = CFG["seguridad"]["hosts_permitidos"]
    with pytest.raises(ValueError):
        validar_host_permitido("example.com", permitidos)
    with pytest.raises(ValueError):   # en lista pero resolviendo a IP pública: también se rechaza
        validar_host_permitido("postgres", permitidos, resolver=lambda h, p: [(2, 1, 6, "", ("8.8.8.8", 0))])
    validar_host_permitido("postgres", permitidos, resolver=lambda h, p: [(2, 1, 6, "", ("172.18.0.2", 0))])
    validar_host_permitido("127.0.0.1", permitidos)


def test_compose_solo_publica_en_loopback():
    texto = (RAIZ / "docker-compose.yml").read_text()
    puertos = re.findall(r'^\s*-\s*"([^"]+:\d+)"\s*(?:#.*)?$', texto, re.M)
    assert puertos and all(p.startswith("127.0.0.1:") for p in puertos)


def test_userlist_y_env_sin_secretos_reales():
    assert cf.generar_userlist({"u": "p"}) == '"u" "p"\n'
    ejemplo = (RAIZ / ".env.example").read_text()
    assert "cambiar_" in ejemplo
    assert ".env\n" in (RAIZ / ".gitignore").read_text()


def test_comparable_con_paper():
    assert cf.comparable_con_paper("paper", 12, 9, "linux")[0] is True
    assert cf.comparable_con_paper("paper", 4, 9, "linux")[0] is False
    assert cf.comparable_con_paper("reducido", 16, 9, "linux")[0] is False
    assert cf.comparable_con_paper("paper", 16, 9, "Docker Desktop")[0] is False
