{#
{
  "kind": "template",
  "description": "Render a read-only DuckLake current-snapshot function.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the function.",
    "function": "PostgreSQL function being created or called.",
    "metadata_schema": "Schema of the catalog database that holds the DuckLake tables.",
    "data_path": "S3 data path for the DuckLake attachment.",
    "user_role": "Role allowed to execute the function."
  }
}
#}
{% from "postgres/macros.sql" import ducklake_attach %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}()
RETURNS bigint AS $$
BEGIN
{{ ducklake_attach(metadata_schema, data_path) }}

  RETURN (
    SELECT r['id']::bigint
    FROM duckdb.query('SELECT id FROM "dl_{{ metadata_schema }}".current_snapshot()') r
  );
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp;

GRANT EXECUTE ON FUNCTION {{ schema }}.{{ function }}() TO {{ user_role }};
