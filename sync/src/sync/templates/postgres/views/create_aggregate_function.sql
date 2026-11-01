{#
{
  "kind": "template",
  "description": "Render the function that runs the aggregate of a request inside DuckDB. It answers null for a request that the table function must serve.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "schema_literal": "SQL literal of the schema name, for the schemas claim check.",
    "app_schema": "PostgreSQL schema that holds the routing functions.",
    "function": "PostgreSQL function being created.",
    "dl_function": "Private DuckLake helper function.",
    "dl_view": "DuckDB view name prepared by the DuckLake helper.",
    "columns": "Structured SQL-safe column metadata. A group column is groupable: text, a whole number, a date, or a flag. A sum or average column is numeric in DuckDB, even when PostgreSQL receives it as text. A min or max column is numeric or a date.",
    "claim_setting": "PostgreSQL session setting containing the current claim.",
    "has_rls": "Enable the row-level security branch.",
    "rls_mappings": "Unit mappings used to build the access-policy predicate.",
    "source_table": "SQL literal name of the source table, as stored in state.",
    "fallbacks": "Ordered list of {name} records for configured fallback sources.",
    "user_role": "Role that reads the table view and so must run this function.",
    "max_rows": "The most rows that the function gives for one request. When the function cuts the answer, it asks for the status 206 and a Content-Range, as PostgREST does."
  }
}
#}
{#
The function checks RLS, plans the sources, and runs the GROUP BY on the DuckLake view. The proxy sends a request with a null answer to the table.
#}
{% from "postgres/macros.sql" import
  name_array,
  require_permission,
  require_scope,
  rls_where_clause,
  sql_array
%}
{% set ordered =
  columns | selectattr('numeric') | list
  + columns | selectattr('pg_type', 'equalto', 'date') | list
%}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}()
RETURNS json AS $$
DECLARE
  v_subject text;
  v_where text;
  v_filter jsonb;
  v_aggregate jsonb;
  v_pinned bigint;
  v_ducklake_only boolean;
  v_plan record;
  v_snapshot bigint;
  v_result text;
  v_duckdb_sql text;
BEGIN
{{ require_permission('read_api') }}
{{ require_scope(schema_literal, "'[]'::json") }}

  v_subject := current_setting('{{ claim_setting }}', true);

{{ rls_where_clause(schema, schema_literal, has_rls, rls_mappings, true) }}
  IF v_where IS NULL THEN
    RETURN '[]'::json;
  END IF;

  v_filter := {{ app_schema }}.request_filter();
  v_aggregate := v_filter -> 'aggregate';
  IF v_aggregate IS NULL THEN
    RETURN NULL;
  END IF;

  v_where := {{ app_schema }}.and_where(
    v_where,
    {{ app_schema }}.render_dl_where(v_filter, {{ sql_array(columns | map(attribute='key')) }})
  );

  -- Only a table that DuckLake serves alone runs here. A fallback source needs the table function.
  v_pinned := {{ app_schema }}.requested_snapshot();
  SELECT coalesce(bool_or(plan.name = 'ducklake' AND plan.use), false)
    AND NOT coalesce(bool_or(plan.name <> 'ducklake' AND plan.use), false)
  INTO v_ducklake_only
  FROM {{ app_schema }}.plan_sources(
    {{ source_table }},
    {{ name_array(fallbacks | map(attribute='name')) }},
    v_pinned IS NOT NULL
  ) AS plan;
  IF NOT v_ducklake_only THEN
    RETURN NULL;
  END IF;

  SELECT * INTO v_plan FROM {{ app_schema }}.plan_aggregate(
    v_aggregate,
    jsonb_build_object(
      'names', {{ sql_array(columns | map(attribute='key')) }},
      'kinds', {{ name_array(columns | map(attribute='pg_type')) }},
      'group', {{ sql_array(columns | selectattr('groupable') | map(attribute='key')) }},
      'count', {{ sql_array(columns | rejectattr('is_json') | map(attribute='key')) }},
      'sum', {{ sql_array(columns | selectattr('numeric') | map(attribute='key')) }},
      'avg', {{ sql_array(columns | selectattr('numeric') | map(attribute='key')) }},
      'min', {{ sql_array(ordered | map(attribute='key')) }},
      'max', {{ sql_array(ordered | map(attribute='key')) }}
    ),
    {{ max_rows }}
  );
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  v_snapshot := coalesce(v_pinned, {{ schema }}.dl_latest_snapshot());
  PERFORM {{ schema }}.{{ dl_function }}(
    v_where, v_snapshot::text, v_pinned IS NOT NULL, NULL, NULL
  );

  -- Without an order, any groups will do, so DuckDB stops after the rows that the answer needs and one more.
  v_duckdb_sql := format(
    'SELECT CAST(json_group_array(to_json(t)) AS VARCHAR) AS j FROM (SELECT %s FROM {{ dl_view }}%s%s) t',
    v_plan.select_list,
    v_plan.group_by,
    CASE
      WHEN v_plan.order_by = '' THEN ' LIMIT ' || (v_plan.row_offset + v_plan.row_take + 1)
      ELSE ''
    END
  );
  EXECUTE format('SELECT r[''j'']::text FROM duckdb.query(%L) r', v_duckdb_sql) INTO v_result;
  v_result := coalesce(v_result, '[]');

  PERFORM {{ app_schema }}.set_response_headers('ducklake', v_snapshot);
  RETURN {{ app_schema }}.page_aggregate(
    v_result,
    v_plan.order_by,
    v_plan.row_offset,
    v_plan.row_take,
    v_plan.row_limit,
    {{ max_rows }}
  )::json;
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp;

REVOKE ALL ON FUNCTION {{ schema }}.{{ function }}() FROM public;
GRANT EXECUTE ON FUNCTION {{ schema }}.{{ function }}() TO {{ user_role }}
