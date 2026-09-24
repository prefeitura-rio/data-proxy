{#
{
  "kind": "template",
  "description": "Create the freshness table with a scoped RLS policy and grant read access to the application role.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "user_role": "Application database role.",
    "rls_schema": "Schema containing row-level security support objects.",
    "scope": "SQL predicate limiting access to the current schema."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ schema }};
GRANT USAGE ON SCHEMA {{ schema }} TO {{ user_role }};
CREATE TABLE IF NOT EXISTS {{ schema }}.freshness (
    "table" text NOT NULL,
    strategy text NOT NULL,
    partition text,
    updated_at timestamptz,
    attempted_at timestamptz NOT NULL,
    status {{ rls_schema }}.sync_status NOT NULL,
    UNIQUE NULLS NOT DISTINCT ("table", strategy, partition)
);
GRANT SELECT ON {{ schema }}.freshness TO {{ user_role }};
ALTER TABLE {{ schema }}.freshness ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS schema_scope ON {{ schema }}.freshness;
CREATE POLICY schema_scope ON {{ schema }}.freshness
USING ({{ scope }})
