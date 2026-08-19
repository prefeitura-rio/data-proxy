{{/*
This file defines the database initialization scripts as Helm templates.
*/}}
{{- define "data-proxy.rolesScript" -}}
#!/bin/bash
# This script creates the database roles and the rls schema.
set -e

AUTH_PASSWORD="${PGRST_AUTHENTICATOR_PASSWORD:-{{ .Values.auth.authenticatorRole }}}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
CREATE ROLE "{{ .Values.auth.anonRole }}" NOLOGIN NOBYPASSRLS;
CREATE ROLE "{{ .Values.auth.userRole }}" NOLOGIN NOBYPASSRLS;
CREATE ROLE "{{ .Values.auth.authenticatorRole }}" NOINHERIT LOGIN PASSWORD '${AUTH_PASSWORD}';
GRANT "{{ .Values.auth.anonRole }}" TO "{{ .Values.auth.authenticatorRole }}";
GRANT "{{ .Values.auth.userRole }}" TO "{{ .Values.auth.authenticatorRole }}";
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
CREATE SCHEMA IF NOT EXISTS {{ .Values.auth.rlsSchema }};
GRANT USAGE ON SCHEMA {{ .Values.auth.rlsSchema }} TO "{{ .Values.auth.anonRole }}";
GRANT USAGE ON SCHEMA {{ .Values.auth.rlsSchema }} TO "{{ .Values.auth.userRole }}";
GRANT USAGE ON SCHEMA {{ .Values.auth.rlsSchema }} TO "{{ .Values.auth.authenticatorRole }}";
EOSQL
{{- end }}

{{- define "data-proxy.preRequestSql" -}}
-- This function mirrors every JWT claim into a session variable
-- (`app.claim_<name>`), read by each protected table's access_policy check
-- using that schema's configured identity claim.
CREATE OR REPLACE FUNCTION {{ .Values.auth.rlsSchema }}.pre_request() RETURNS void AS $$
DECLARE
    claims json := coalesce(current_setting('request.jwt.claims', true)::json, '{}'::json);
    claim_name text;
    claim_value json;
BEGIN
    FOR claim_name, claim_value IN SELECT * FROM json_each(claims) LOOP
        PERFORM set_config(
            'app.claim_' || claim_name,
            CASE json_typeof(claim_value)
                WHEN 'array' THEN (
                    SELECT string_agg(value, ',')
                    FROM json_array_elements_text(claim_value)
                )
                ELSE trim(both '"' FROM claim_value::text)
            END,
            true
        );
    END LOOP;
END;
$$ LANGUAGE plpgsql;
{{- end }}

{{- define "data-proxy.accessPolicySql" -}}
-- Generic, per-request access grants shared by every protected table in
-- every schema. The backend writes this table directly through PostgREST
-- (role policy_writer_<schema>, granted per schema at sync time); Data Proxy
-- only ever reads it to enforce row visibility, never to compute grants.
CREATE TABLE IF NOT EXISTS {{ .Values.auth.rlsSchema }}.access_policy (
    schema text NOT NULL,
    subject text NOT NULL,
    is_super_admin boolean NOT NULL DEFAULT false,
    unit_type text,
    unit_id text,
    UNIQUE (schema, subject, unit_type, unit_id)
);

ALTER TABLE {{ .Values.auth.rlsSchema }}.access_policy ENABLE ROW LEVEL SECURITY;

GRANT SELECT ON {{ .Values.auth.rlsSchema }}.access_policy TO "{{ .Values.auth.userRole }}";

-- The real access decision is enforced by each protected table's own policy
-- (which matches schema, subject, and unit membership explicitly). This
-- policy only controls direct reads of access_policy itself, which is not an
-- externally exposed API schema.
DROP POLICY IF EXISTS user_read ON {{ .Values.auth.rlsSchema }}.access_policy;
CREATE POLICY user_read ON {{ .Values.auth.rlsSchema }}.access_policy
FOR SELECT
TO "{{ .Values.auth.userRole }}"
USING (true);
{{- end }}
