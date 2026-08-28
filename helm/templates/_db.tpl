{{/*
This file defines the database initialization scripts as Helm templates.
*/}}
{{- define "data-proxy.rolesScript" -}}
#!/bin/bash
# This script creates the database roles and the rls schema.
set -e

AUTH_PASSWORD="${PGRST_AUTHENTICATOR_PASSWORD:-{{ .Values.auth.authenticatorRole }}}"

psql -v ON_ERROR_STOP=1 -v authenticator_password="$AUTH_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{{ .Values.auth.anonRole }}') THEN
        CREATE ROLE "{{ .Values.auth.anonRole }}";
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{{ .Values.auth.userRole }}') THEN
        CREATE ROLE "{{ .Values.auth.userRole }}";
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{{ .Values.auth.authenticatorRole }}') THEN
        CREATE ROLE "{{ .Values.auth.authenticatorRole }}";
    END IF;
END
\$\$;
ALTER ROLE "{{ .Values.auth.anonRole }}" NOLOGIN NOBYPASSRLS;
ALTER ROLE "{{ .Values.auth.userRole }}" NOLOGIN NOBYPASSRLS;
ALTER ROLE "{{ .Values.auth.authenticatorRole }}" NOINHERIT LOGIN NOBYPASSRLS PASSWORD :'authenticator_password';
GRANT "{{ .Values.auth.anonRole }}" TO "{{ .Values.auth.authenticatorRole }}";
GRANT "{{ .Values.auth.userRole }}" TO "{{ .Values.auth.authenticatorRole }}";
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
CREATE SCHEMA IF NOT EXISTS {{ .Values.auth.rlsSchema }};
GRANT USAGE ON SCHEMA {{ .Values.auth.rlsSchema }} TO "{{ .Values.auth.anonRole }}";
GRANT USAGE ON SCHEMA {{ .Values.auth.rlsSchema }} TO "{{ .Values.auth.userRole }}";
GRANT USAGE ON SCHEMA {{ .Values.auth.rlsSchema }} TO "{{ .Values.auth.authenticatorRole }}";
CREATE SCHEMA IF NOT EXISTS rls;
DO \$\$
BEGIN
    CREATE TYPE rls.sync_status AS ENUM ('success', 'failure');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
\$\$;
{{- range $schema, $_ := (.Values.syncConfig.schemas | default dict) }}
CREATE SCHEMA IF NOT EXISTS "{{ $schema }}";
GRANT USAGE ON SCHEMA "{{ $schema }}" TO "{{ $.Values.auth.userRole }}";
CREATE TABLE IF NOT EXISTS "{{ $schema }}".freshness (
    "table" text NOT NULL,
    strategy text NOT NULL,
    partition text,
    updated_at timestamptz,
    attempted_at timestamptz NOT NULL,
    status rls.sync_status NOT NULL,
    UNIQUE NULLS NOT DISTINCT ("table", strategy, partition)
);
GRANT SELECT ON "{{ $schema }}".freshness TO "{{ $.Values.auth.userRole }}";
ALTER TABLE "{{ $schema }}".freshness ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS schema_scope ON "{{ $schema }}".freshness;
CREATE POLICY schema_scope ON "{{ $schema }}".freshness
USING (
    '{{ $schema }}' = ANY(
        string_to_array(current_setting('app.claim_schemas', true), ',')
    )
);
{{- end }}
EOSQL
{{- if .Values.backup.enabled }}

BACKUP_PASSWORD="${BACKUP_PASSWORD:?BACKUP_PASSWORD is required when backup.enabled is true}"

psql -v ON_ERROR_STOP=1 -v backup_password="$BACKUP_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backup') THEN
        CREATE ROLE backup;
    END IF;
END
\$\$;
ALTER ROLE backup NOINHERIT LOGIN NOBYPASSRLS PASSWORD :'backup_password';
{{- range $schema, $_ := .Values.syncConfig.schemas }}
GRANT USAGE ON SCHEMA "{{ $schema }}" TO backup;
{{- end }}
EOSQL
{{- end }}
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
{{- range $schema, $_ := .Values.syncConfig.schemas }}
CREATE TABLE IF NOT EXISTS {{ $schema }}.access_policy (
    subject text NOT NULL,
    is_admin boolean NOT NULL DEFAULT false,
    is_enabled boolean NOT NULL DEFAULT true,
    unit_type text,
    unit_id text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (subject, unit_type, unit_id)
);

ALTER TABLE {{ $schema }}.access_policy ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION {{ $schema }}.set_access_policy_metadata_timestamps()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.metadata := coalesce(NEW.metadata, '{}'::jsonb)
            || jsonb_build_object('created_at', now(), 'updated_at', now());
    ELSE
        NEW.metadata := coalesce(NEW.metadata, '{}'::jsonb)
            || jsonb_build_object(
                'created_at', coalesce(OLD.metadata->'created_at', to_jsonb(now())),
                'updated_at', now()
            );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS access_policy_metadata_timestamps ON {{ $schema }}.access_policy;
CREATE TRIGGER access_policy_metadata_timestamps
BEFORE INSERT OR UPDATE ON {{ $schema }}.access_policy
FOR EACH ROW EXECUTE FUNCTION {{ $schema }}.set_access_policy_metadata_timestamps();

GRANT SELECT ON {{ $schema }}.access_policy TO "{{ $.Values.auth.userRole }}";
{{- if $.Values.backup.enabled }}
GRANT SELECT ON {{ $schema }}.access_policy TO backup;
{{- end }}

DROP POLICY IF EXISTS user_read ON {{ $schema }}.access_policy;
CREATE POLICY user_read ON {{ $schema }}.access_policy
FOR SELECT
TO "{{ $.Values.auth.userRole }}"
USING ({{ printf "'%s'" $schema }} = ANY(string_to_array(current_setting('app.claim_schemas', true), ',')));
{{- if $.Values.backup.enabled }}
DROP POLICY IF EXISTS backup_read ON {{ $schema }}.access_policy;
CREATE POLICY backup_read ON {{ $schema }}.access_policy
FOR SELECT
TO backup
USING (true);
{{- end }}
{{- end }}
{{- end }}
