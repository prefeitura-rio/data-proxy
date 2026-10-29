{#
{
  "kind": "template",
  "description": "Render the function that runs the aggregate of a request inside DuckDB: check RLS, plan the sources, and run the GROUP BY on the DuckLake view. The function answers null for a request that the table function must serve, so the proxy sends that request to the table.",
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
-- noqa: disable=CP02,PRS,LT05
{% from "postgres/macros.sql" import rls_where_clause, require_permission, require_scope %}
CREATE OR REPLACE FUNCTION {{ schema }}.{{ function }}()
RETURNS json AS $$
DECLARE
  v_subject text;
  v_where text;
  v_filter jsonb;
  v_aggregate jsonb;
  v_pinned bigint;
  v_ducklake_only boolean;
  v_snapshot bigint;
  v_item jsonb;
  v_column text;
  v_alias text;
  v_type text;
  v_expression text;
  v_select text[] := ARRAY[]::text[];
  v_groups integer := 0;
  v_order text[] := ARRAY[]::text[];
  v_names text[] := ARRAY[{% for column in columns %}{{ column.key }}{% if not loop.last %}, {% endif %}{% endfor %}]::text[];
  v_kinds text[] := ARRAY[{% for column in columns %}'{{ column.pg_type }}'{% if not loop.last %}, {% endif %}{% endfor %}]::text[];
  v_offset bigint;
  v_limit bigint;
  v_take bigint;
  v_returned bigint;
  v_truncated boolean;
  v_result text;
BEGIN
{{ require_permission('read_api') }}
{{ require_scope(schema_literal, "'[]'::json") }}

  v_subject := current_setting('{{ claim_setting }}', true);

{{ rls_where_clause(schema, schema_literal, has_rls, rls_mappings, "'[]'::json") }}

  v_filter := {{ app_schema }}.request_filter();
  v_aggregate := v_filter -> 'aggregate';
  IF v_aggregate IS NULL THEN
    RETURN NULL;
  END IF;

  v_where := {{ app_schema }}.and_where(
    v_where,
    {{ app_schema }}.render_dl_where(
      v_filter,
      ARRAY[{% for column in columns %}{{ column.key }}{% if not loop.last %}, {% endif %}{% endfor %}]::text[]
    )
  );

  -- Only a table that DuckLake serves alone runs here. A fallback source needs the table function.
  v_pinned := {{ app_schema }}.requested_snapshot();
  SELECT coalesce(bool_or(plan.name = 'ducklake' AND plan.use), false)
    AND NOT coalesce(bool_or(plan.name <> 'ducklake' AND plan.use), false)
  INTO v_ducklake_only
  FROM {{ app_schema }}.plan_sources(
    {{ source_table }},
    ARRAY[{% for f in fallbacks %}'{{ f.name }}'{% if not loop.last %}, {% endif %}{% endfor %}]::text[],
    v_pinned IS NOT NULL
  ) AS plan;
  IF NOT v_ducklake_only THEN
    RETURN NULL;
  END IF;

  IF jsonb_typeof(v_aggregate -> 'group') = 'array' THEN
    FOR v_item IN SELECT jsonb_array_elements(v_aggregate -> 'group') LOOP
      v_column := v_item ->> 'column';
      v_alias := v_item ->> 'alias';
      IF v_alias IS NULL OR NOT v_column = ANY(ARRAY[{% for column in columns if column.groupable %}{{ column.key }}{% if not loop.last %}, {% endif %}{% endfor %}]::text[]) THEN
        RETURN NULL;
      END IF;
      v_select := v_select || format('%I AS %I', v_column, v_alias);
      v_groups := v_groups + 1;
    END LOOP;
  END IF;

  -- PostgreSQL orders and cuts the rows of the groups, so the text order follows its collation.
  -- The order names a column of the group, and the column has a type that PostgreSQL sorts from its text.
  FOR v_item IN SELECT jsonb_array_elements(v_aggregate -> 'order') LOOP
    v_column := v_item ->> 'column';
    v_alias := (
      SELECT entry ->> 'alias'
      FROM jsonb_array_elements(coalesce(v_aggregate -> 'group', '[]'::jsonb)) AS entry
      WHERE entry ->> 'column' = v_column
      LIMIT 1
    );
    v_type := v_kinds[array_position(v_names, v_column)];
    IF v_alias IS NULL OR v_type IS NULL OR v_type NOT IN ('text', 'bigint', 'date', 'boolean') THEN
      RETURN NULL;
    END IF;
    v_order := v_order || format(
      '(t.elem ->> %L)%s %s%s',
      v_alias,
      CASE WHEN v_type = 'text' THEN '' ELSE '::' || v_type END,
      CASE WHEN (v_item ->> 'descending')::boolean THEN 'DESC' ELSE 'ASC' END,
      CASE v_item ->> 'nulls' WHEN 'first' THEN ' NULLS FIRST' WHEN 'last' THEN ' NULLS LAST' ELSE '' END
    );
  END LOOP;
  v_offset := coalesce((v_aggregate ->> 'offset')::bigint, 0);
  v_limit := (v_aggregate ->> 'limit')::bigint;
  v_take := least(coalesce(v_limit, {{ max_rows }}), {{ max_rows }});

  FOR v_item IN SELECT jsonb_array_elements(v_aggregate -> 'items') LOOP
    v_column := v_item ->> 'column';
    v_expression := CASE v_item ->> 'fn'
      WHEN 'count' THEN CASE
        WHEN v_column IS NULL THEN 'count(*)'
        WHEN v_column = ANY(ARRAY[{% for column in columns if not column.is_json %}{{ column.key }}{% if not loop.last %}, {% endif %}{% endfor %}]::text[]) THEN format('count(%I)', v_column)
      END
      WHEN 'sum' THEN CASE
        WHEN v_column = ANY(ARRAY[{% for column in columns if column.numeric %}{{ column.key }}{% if not loop.last %}, {% endif %}{% endfor %}]::text[]) THEN format('sum(%I)', v_column)
      END
      WHEN 'avg' THEN CASE
        WHEN v_column = ANY(ARRAY[{% for column in columns if column.numeric %}{{ column.key }}{% if not loop.last %}, {% endif %}{% endfor %}]::text[]) THEN format('avg(%I)', v_column)
      END
      WHEN 'min' THEN CASE
        WHEN v_column = ANY(ARRAY[{% for column in columns if column.numeric or column.pg_type == 'date' %}{{ column.key }}{% if not loop.last %}, {% endif %}{% endfor %}]::text[]) THEN format('min(%I)', v_column)
      END
      WHEN 'max' THEN CASE
        WHEN v_column = ANY(ARRAY[{% for column in columns if column.numeric or column.pg_type == 'date' %}{{ column.key }}{% if not loop.last %}, {% endif %}{% endfor %}]::text[]) THEN format('max(%I)', v_column)
      END
    END;
    IF v_expression IS NULL OR (v_item ->> 'alias') IS NULL THEN
      RETURN NULL;
    END IF;
    v_select := v_select || format('%s AS %I', v_expression, v_item ->> 'alias');
  END LOOP;

  v_snapshot := coalesce(v_pinned, {{ schema }}.dl_latest_snapshot());
  PERFORM {{ schema }}.{{ dl_function }}(
    v_where, v_snapshot::text, v_pinned IS NOT NULL, NULL, NULL
  );

  EXECUTE format(
    'SELECT r[''j'']::text FROM duckdb.query(%L) r',
    format(
      'SELECT CAST(json_group_array(to_json(t)) AS VARCHAR) AS j FROM (SELECT %s FROM {{ dl_view }}%s%s) t',
      array_to_string(v_select, ', '),
      CASE
        WHEN v_groups > 0 THEN ' GROUP BY ' || (SELECT string_agg(ordinal::text, ', ') FROM generate_series(1, v_groups) AS ordinal)
        ELSE ''
      END,
      -- Without an order, any groups will do, so DuckDB stops after the rows that the answer needs and one more.
      CASE
        WHEN cardinality(v_order) = 0 THEN ' LIMIT ' || (v_offset + v_take + 1)
        ELSE ''
      END
    )
  ) INTO v_result;
  v_result := coalesce(v_result, '[]');

  -- One row more than the answer holds shows that the cap cut it.
  EXECUTE format(
    'SELECT coalesce(json_agg(s.elem ORDER BY s.rn) FILTER (WHERE s.rn <= %s), ''[]''::json)::text, count(*) '
    || 'FROM (SELECT t.elem, row_number() OVER (%s) AS rn FROM json_array_elements($1::json) AS t(elem)) s '
    || 'WHERE s.rn > %s AND s.rn <= %s',
    v_offset + v_take,
    CASE WHEN cardinality(v_order) > 0 THEN 'ORDER BY ' || array_to_string(v_order, ', ') ELSE '' END,
    v_offset,
    v_offset + v_take + 1
  ) INTO v_result, v_returned USING v_result;
  v_truncated := v_returned > v_take AND (v_limit IS NULL OR v_limit > {{ max_rows }});

  PERFORM {{ app_schema }}.set_response_headers('ducklake', v_snapshot);
  IF v_truncated THEN
    PERFORM set_config('response.status', '206', true);
    PERFORM set_config(
      'response.headers',
      (
        current_setting('response.headers')::jsonb
        || jsonb_build_array(jsonb_build_object('Content-Range', format('%s-%s/*', v_offset, v_offset + v_take - 1)))
      )::text,
      true
    );
  END IF;
  RETURN v_result::json;
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp;

REVOKE ALL ON FUNCTION {{ schema }}.{{ function }}() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {{ schema }}.{{ function }}() TO {{ user_role }}
