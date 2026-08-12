{{/*
This file defines the database initialization scripts as Helm templates.
*/}}
{{- define "data-proxy.rolesScript" -}}
#!/bin/bash
# This script creates the database roles and the rls schema.
set -e

AUTH_PASSWORD="${PGRST_AUTHENTICATOR_PASSWORD:-{{ .Values.auth.authenticatorRole }}}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
CREATE ROLE {{ .Values.auth.anonRole }} NOLOGIN NOBYPASSRLS;
CREATE ROLE {{ .Values.auth.userRole }} NOLOGIN NOBYPASSRLS;
CREATE ROLE {{ .Values.auth.authenticatorRole }} NOINHERIT LOGIN PASSWORD '${AUTH_PASSWORD}';
GRANT {{ .Values.auth.anonRole }} TO {{ .Values.auth.authenticatorRole }};
GRANT {{ .Values.auth.userRole }} TO {{ .Values.auth.authenticatorRole }};
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
CREATE SCHEMA IF NOT EXISTS {{ .Values.auth.rlsSchema }};
GRANT USAGE ON SCHEMA {{ .Values.auth.rlsSchema }} TO {{ .Values.auth.anonRole }};
GRANT USAGE ON SCHEMA {{ .Values.auth.rlsSchema }} TO {{ .Values.auth.userRole }};
GRANT USAGE ON SCHEMA {{ .Values.auth.rlsSchema }} TO {{ .Values.auth.authenticatorRole }};
EOSQL
{{- end }}

{{- define "data-proxy.preRequestSql" -}}
-- This function reads the JWT claims and sets the session variable for row-level security.
CREATE OR REPLACE FUNCTION {{ .Values.auth.rlsSchema }}.pre_request() RETURNS void AS $$
BEGIN
    PERFORM set_config(
        '{{ .Values.auth.sessionVar }}',
        coalesce(
            (
                SELECT string_agg(value, ',')
                FROM json_array_elements_text(
                    coalesce(
                        current_setting('request.jwt.claims', true)::json -> '{{ .Values.auth.jwtClaim }}',
                        '[]'::json
                    )
                )
            ),
            ''
        ),
        true
    );
END;
$$ LANGUAGE plpgsql;
{{- end }}
