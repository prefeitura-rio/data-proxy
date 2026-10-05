{#
{
  "kind": "template",
  "description": "Render the function behind a table view: check RLS, plan the sources, and read them.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "app_schema": "PostgreSQL schema that holds the routing functions.",
    "function": "PostgreSQL function being created.",
    "dl_function": "Private DuckLake helper function.",
    "dl_view": "DuckDB view name prepared by the DuckLake helper.",
    "columns": "Structured SQL-safe column metadata.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "has_rls": "Enable the row-level security branch.",
    "rls_mappings": "Unit mappings used to build the access-policy predicate.",
    "source_table": "SQL literal name of the source table, as stored in state.",
    "fallbacks": "Ordered list of {name, function, view} records for configured fallback sources.",
    "user_role": "Role that reads the table view and so must run this function."
  }
}
#}
-- noqa: disable=PRS,LT05
{% from "postgres/macros.sql" import rls_where_clause, return_query_select, duckdb_query_select %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}()
RETURNS TABLE(
{% for column in columns %}
{{ column.name }} {{ column.return_type }}{% if not loop.last %}, {% endif %}
{% endfor %}
) AS $$
DECLARE
  v_subject text;
  v_where text;
  v_pinned bigint;
  v_snapshot bigint;
  v_plan record;
  v_sources text[] := ARRAY[]::text[];
BEGIN
  v_subject := current_setting('{{ claim_setting }}', true);

{{ rls_where_clause(schema, has_rls, rls_mappings) }}

  v_pinned := {{ app_schema }}.requested_snapshot();

  FOR v_plan IN
    SELECT * FROM {{ app_schema }}.plan_sources(
      {{ source_table }},
      ARRAY[{% for f in fallbacks %}'{{ f.name }}'{% if not loop.last %}, {% endif %}{% endfor %}]::text[],
      v_pinned IS NOT NULL
    )
  LOOP
    IF NOT v_plan.use THEN
      CONTINUE;
    END IF;

    v_sources := v_sources || ARRAY[v_plan.name];

    IF v_plan.name = 'ducklake' THEN
      v_snapshot := coalesce(v_pinned, {{ schema }}.ducklake_latest_snapshot());
      PERFORM {{ schema }}.{{ dl_function }}(v_where, v_snapshot::text);
      RETURN QUERY
{{ return_query_select(columns) }}
{{ duckdb_query_select(columns, dl_view) }};
{% for f in fallbacks %}
    ELSIF v_plan.name = '{{ f.name }}' THEN
      PERFORM {{ schema }}.{{ f.function }}(v_where, v_plan.arg);
      RETURN QUERY
{{ return_query_select(columns) }}
{{ duckdb_query_select(columns, f.view) }};
{% endfor %}
    END IF;
  END LOOP;

  PERFORM {{ app_schema }}.set_response_headers(
    {{ app_schema }}.source_label(v_sources),
    v_snapshot
  );
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp;

REVOKE ALL ON FUNCTION {{ schema }}.{{ function }}() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {{ schema }}.{{ function }}() TO {{ user_role }}
