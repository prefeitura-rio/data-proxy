#!/bin/bash
# Bootstraps the local Postgres database for docker-compose: extensions,
# roles, per-schema freshness tables, the RLS pre_request function, and
# access_policy. Mirrors helm/templates/_db.tpl for local development.
# Keep both in sync by hand: this file intentionally has no build step.
set -e

AUTH_PASSWORD="${PGRST_AUTHENTICATOR_PASSWORD:-authenticator}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
-- Extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_duckdb;

-- Roles
CREATE ROLE "anon" NOLOGIN NOBYPASSRLS;
CREATE ROLE "user" NOLOGIN NOBYPASSRLS;
CREATE ROLE "authenticator" NOINHERIT LOGIN PASSWORD '${AUTH_PASSWORD}';
GRANT "anon" TO "authenticator";
GRANT "user" TO "authenticator";

-- rls schema
CREATE SCHEMA IF NOT EXISTS rls;
DO \$\$
BEGIN
    CREATE TYPE rls.sync_status AS ENUM ('success', 'failure');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
\$\$;

-- pre_request: mirrors every JWT claim into a session variable
-- (app.claim_<name>), read by each protected table's access_policy check
-- using that schema's configured identity claim.
CREATE OR REPLACE FUNCTION rls.pre_request() RETURNS void AS \$\$
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
\$\$ LANGUAGE plpgsql;

-- access_policy: generic, per-request access grants shared by every
-- protected table in every schema. The backend writes this table directly
-- through PostgREST; Data Proxy only ever reads it to enforce row
-- visibility, never to compute grants.
CREATE TABLE IF NOT EXISTS rls.access_policy (
    schema text NOT NULL,
    subject text NOT NULL,
    is_admin boolean NOT NULL DEFAULT false,
    is_enabled boolean NOT NULL DEFAULT true,
    unit_type text,
    unit_id text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (schema, subject, unit_type, unit_id)
);
ALTER TABLE rls.access_policy ENABLE ROW LEVEL SECURITY;

-- created_at and updated_at live inside metadata. Postgres sets both on
-- every write; a client-supplied value for either key is always replaced.
CREATE OR REPLACE FUNCTION rls.set_access_policy_metadata_timestamps()
RETURNS trigger AS \$\$
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
\$\$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS access_policy_metadata_timestamps ON rls.access_policy;
CREATE TRIGGER access_policy_metadata_timestamps
BEFORE INSERT OR UPDATE ON rls.access_policy
FOR EACH ROW EXECUTE FUNCTION rls.set_access_policy_metadata_timestamps();

GRANT SELECT ON rls.access_policy TO "user";

DROP POLICY IF EXISTS user_read ON rls.access_policy;
CREATE POLICY user_read ON rls.access_policy
FOR SELECT
TO "user"
USING (schema = ANY(string_to_array(current_setting('app.claim_schemas', true), ',')));
EOSQL

# Per-schema setup, matching config/sync.test.json.
for SCHEMA in app_pequenos_cariocas projeto_pequenos_cariocas; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
CREATE SCHEMA IF NOT EXISTS "${SCHEMA}";
GRANT USAGE ON SCHEMA "${SCHEMA}" TO "user";
CREATE TABLE IF NOT EXISTS "${SCHEMA}".freshness (
    "table" text NOT NULL,
    strategy text NOT NULL,
    partition text,
    updated_at timestamptz,
    attempted_at timestamptz NOT NULL,
    status rls.sync_status NOT NULL,
    UNIQUE NULLS NOT DISTINCT ("table", strategy, partition)
);
GRANT SELECT ON "${SCHEMA}".freshness TO "user";
ALTER TABLE "${SCHEMA}".freshness ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS schema_scope ON "${SCHEMA}".freshness;
CREATE POLICY schema_scope ON "${SCHEMA}".freshness
USING (
    '${SCHEMA}' = ANY(
        string_to_array(current_setting('app.claim_schemas', true), ',')
    )
);
EOSQL
done
