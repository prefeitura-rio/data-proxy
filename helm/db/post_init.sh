#!/bin/bash
# This script runs the database initialization files after Patroni creates the first cluster leader.
set -e

SCRIPTS="$(dirname "$0")"

export PGPASSWORD="${PATRONI_SUPERUSER_PASSWORD}"
export PGHOST="127.0.0.1"
export PGPORT="5432"
export PGUSER="${PATRONI_SUPERUSER_USERNAME}"
export POSTGRES_USER="${PATRONI_SUPERUSER_USERNAME}"

psql "$1" -c "CREATE DATABASE \"${POSTGRES_DB}\" OWNER \"${PGUSER}\";"

psql -d "${POSTGRES_DB}" -f "${SCRIPTS}/01_extensions.sql"
bash "${SCRIPTS}/02_roles.sh"
psql -d "${POSTGRES_DB}" -f "${SCRIPTS}/03_pre_request.sql"
psql -d "${POSTGRES_DB}" -f "${SCRIPTS}/04_access_policy.sql"
