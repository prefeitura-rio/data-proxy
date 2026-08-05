#!/bin/bash
# This script creates the database roles and the rls schema.
set -e

AUTH_PASSWORD="${PGRST_AUTHENTICATOR_PASSWORD:-authenticator}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
CREATE ROLE web_anon NOLOGIN NOBYPASSRLS;
CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD '${AUTH_PASSWORD}';
GRANT web_anon TO authenticator;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
CREATE SCHEMA IF NOT EXISTS rls;
GRANT USAGE ON SCHEMA rls TO web_anon;
GRANT USAGE ON SCHEMA rls TO authenticator;
EOSQL
