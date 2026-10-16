#!/bin/sh
# Se ejecuta UNA vez, al crear el volumen de datos (docker-entrypoint-initdb.d).
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     -v core_pw="$APP_CORE_PASSWORD" -v portal_pw="$APP_PORTAL_PASSWORD" <<'SQL'
CREATE ROLE app_core   LOGIN PASSWORD :'core_pw';
CREATE ROLE app_portal LOGIN PASSWORD :'portal_pw';
-- pgbench -i (ejecutado por el administrador) recrea las tablas: los privilegios
-- por defecto garantizan que ambos roles puedan usarlas.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO app_core, app_portal;
SQL
