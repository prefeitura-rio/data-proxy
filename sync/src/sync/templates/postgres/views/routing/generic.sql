{#
{
  "kind": "template",
  "description": "Install the generic functions agg, hll and changes of the routing schema. Each one reads the facts of a table from meta when it runs, so one function serves every table. The wrappers of the data schemas call them.",
  "inputs": {
    "schema": "PostgreSQL schema that holds the routing functions."
  }
}
#}
-- noqa: disable=LT05
{#
The wrappers are SECURITY DEFINER functions of the data schemas, and the request role cannot call these functions itself.
Every function checks the permission and the schemas claim of the token, and it never builds SQL from a name that
meta does not list. A refused table and an unknown table give the same error.
#}

-- The names of the columns of a table that have one flag, or every name for no flag.
CREATE OR REPLACE FUNCTION {{ schema }}.meta_columns(
  p_columns jsonb, p_flag text DEFAULT NULL
) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
  SELECT coalesce(jsonb_agg(c.value ->> 'name' ORDER BY c.ordinality), '[]'::jsonb)
  FROM jsonb_array_elements(p_columns) WITH ORDINALITY AS c(value, ordinality)
  WHERE p_flag IS NULL OR (c.value ->> p_flag)::boolean
$$;

-- The facts of one table, after the checks of the token: the permission, the schemas claim and the table.
CREATE OR REPLACE FUNCTION {{ schema }}.require_table(
  p_schema text, p_table text, p_permission text
) RETURNS {{ schema }}.meta
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_meta {{ schema }}.meta;
BEGIN
  IF NOT rbac.can(p_permission) THEN
    RAISE EXCEPTION 'The token lacks the % permission', p_permission
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  IF rbac.mine(p_schema) THEN
    SELECT * INTO v_meta FROM {{ schema }}.meta m
    WHERE m.schema_name = p_schema AND m.table_name = p_table;
  END IF;

  IF v_meta.table_name IS NULL THEN
    RAISE EXCEPTION 'The token has no access to this table'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN v_meta;
END;
$$;

-- The row policy of a table as a DuckDB WHERE clause: empty for every row, NULL for no row.
-- A token with schema:* for the schema, or with *, or a subject with the grant (*, *) in a scope that covers
-- the table, reads every row. Every other request reads the rows of its unit grants, so a table with no
-- rules, which has no unit mapping, returns no row.
-- The order of the units in a list is not part of the policy: IN ('a','b') and IN ('b','a') match the same rows,
-- so the list has no ORDER BY, which cost 5 ms for a subject with 2,000 grants.
CREATE OR REPLACE FUNCTION {{ schema }}.rls_where(
  p_schema text,
  p_scope text,
  p_subject text,
  p_mappings jsonb,
  p_list_form boolean
) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_where text;
BEGIN
  IF rbac.whole(p_schema) OR EXISTS (
    SELECT 1 FROM access.policy p
    WHERE p.subject = p_subject AND access.covers(p.scope, p_scope)
      AND p.unit_type = '*' AND p.unit_id = '*'
  ) THEN
    RETURN '';
  END IF;

  SELECT string_agg(term, ' OR ') INTO v_where
  FROM (
    SELECT
      CASE WHEN count(*) = 1 THEN min(rule_filter)
        ELSE '(' || string_agg(rule_filter, ' AND ' ORDER BY col, ut) || ')'
      END AS term
    FROM (
      SELECT
        t.col,
        t.ut,
        coalesce(t.grp, -row_number() OVER (ORDER BY t.col, t.ut)) AS key,
        CASE
          WHEN count(p.unit_id) = 0 THEN NULL
          WHEN t.ty = 'VARCHAR[]' THEN
            format('list_has_any(%I, [%s])', t.col, string_agg(quote_literal(p.unit_id), ','))
          ELSE format(
            CASE WHEN p_list_form
              THEN 'CAST(%I AS VARCHAR) IN (SELECT unnest([%s]))'
              ELSE '%I IN (%s)'
            END,
            t.col,
            string_agg(quote_literal(p.unit_id), ',')
          )
        END AS rule_filter
      FROM jsonb_to_recordset(p_mappings) AS t(col text, ut text, ty text, grp integer)
      LEFT JOIN access.policy p
        ON p.unit_type = t.ut AND p.subject = p_subject
        AND access.covers(p.scope, p_scope)
      GROUP BY t.grp, t.col, t.ty, t.ut
    ) AS rules
    GROUP BY key
    HAVING bool_and(rule_filter IS NOT NULL)
  ) AS terms;

  IF v_where IS NULL THEN
    RETURN NULL;
  END IF;
  RETURN 'WHERE (' || v_where || ')';
END;
$$;

-- Attach the read-only DuckLake catalog of one schema under the alias dl_<schema>, after the allocator
-- of DuckDB is set to purge freed memory in the background.
CREATE OR REPLACE FUNCTION {{ schema }}.attach_ducklake(
  p_schema text, p_data_path text
) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM duckdb.raw_query(
    'SET allocator_background_threads = true; SET allocator_flush_threshold = ''16MiB'''
  );
  PERFORM duckdb.raw_query(format(
    'ATTACH IF NOT EXISTS %L AS %I (DATA_PATH %L, METADATA_SCHEMA %L, READ_ONLY)',
    'ducklake:ducklake_catalog', 'dl_' || p_schema, p_data_path, p_schema
  ));
END;
$$;

-- Run the aggregate of a request in DuckDB. The answer is null for a request that the table function must serve.
CREATE OR REPLACE FUNCTION {{ schema }}.agg(p_schema text, p_table text) RETURNS json
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp AS $$
DECLARE
  v_meta {{ schema }}.meta;
  v_names text[];
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
  v_meta := {{ schema }}.require_table(p_schema, p_table, 'read_api');
  v_where := {{ schema }}.rls_where(
    p_schema, v_meta.scope_path, current_setting(v_meta.claim_setting, true), v_meta.rls_mappings, true
  );
  IF v_where IS NULL THEN
    RETURN '[]'::json;
  END IF;

  v_filter := {{ schema }}.request_filter();
  v_aggregate := v_filter -> 'aggregate';
  IF v_aggregate IS NULL THEN
    RETURN NULL;
  END IF;

  v_names := ARRAY(SELECT jsonb_array_elements_text({{ schema }}.meta_columns(v_meta.columns)));
  v_where := {{ schema }}.and_where(v_where, {{ schema }}.render_dl_where(v_filter, v_names));

  -- Only a table that DuckLake serves alone runs here. A fallback source needs the table function.
  v_pinned := {{ schema }}.requested_snapshot();
  SELECT coalesce(bool_or(plan.name = 'ducklake' AND plan.use), false)
    AND NOT coalesce(bool_or(plan.name <> 'ducklake' AND plan.use), false)
  INTO v_ducklake_only
  FROM {{ schema }}.plan_sources(
    v_meta.source_table,
    ARRAY(SELECT f ->> 'name' FROM jsonb_array_elements(v_meta.fallbacks) AS f),
    v_pinned IS NOT NULL
  ) AS plan;
  IF NOT v_ducklake_only THEN
    RETURN NULL;
  END IF;

  SELECT * INTO v_plan FROM {{ schema }}.plan_aggregate(
    v_aggregate,
    jsonb_build_object(
      'names', {{ schema }}.meta_columns(v_meta.columns),
      'kinds', (
        SELECT coalesce(jsonb_agg(c.value ->> 'pg_type' ORDER BY c.ordinality), '[]'::jsonb)
        FROM jsonb_array_elements(v_meta.columns) WITH ORDINALITY AS c(value, ordinality)
      ),
      'group', {{ schema }}.meta_columns(v_meta.columns, 'groupable'),
      'count', {{ schema }}.meta_columns(v_meta.columns, 'countable'),
      'sum', {{ schema }}.meta_columns(v_meta.columns, 'exact_numeric'),
      'avg', {{ schema }}.meta_columns(v_meta.columns, 'exact_numeric'),
      'timestamps', {{ schema }}.meta_columns(v_meta.columns, 'timestamp'),
      'min', {{ schema }}.meta_columns(v_meta.columns, 'ordered'),
      'max', {{ schema }}.meta_columns(v_meta.columns, 'ordered')
    ),
    v_meta.max_rows
  );
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  v_snapshot := coalesce(v_pinned, {{ schema }}.snapshot(p_schema));
  EXECUTE format('SELECT %I.%I($1, $2, $3, NULL, NULL)', p_schema, p_table || '_dl_fn')
    USING v_where, v_snapshot::text, v_pinned IS NOT NULL;

  -- Without an order, any groups will do, so DuckDB stops after the rows that the answer needs and one more.
  -- An order that sorts the same way in both engines is cut in DuckDB too, so only those groups reach PostgreSQL.
  v_duckdb_sql := format(
    'SELECT CAST(json_group_array(to_json(t)) AS VARCHAR) AS j FROM (SELECT %s FROM %I%s%s) t',
    v_plan.select_list,
    v_meta.view,
    v_plan.group_by,
    CASE
      WHEN v_plan.order_by = '' THEN ' LIMIT ' || (v_plan.row_offset + v_plan.row_take + 1)
      ELSE {{ schema }}.plan_aggregate_tail(
        v_aggregate,
        jsonb_build_object(
          'names', {{ schema }}.meta_columns(v_meta.columns),
          'kinds', (
            SELECT coalesce(jsonb_agg(c.value ->> 'pg_type' ORDER BY c.ordinality), '[]'::jsonb)
            FROM jsonb_array_elements(v_meta.columns) WITH ORDINALITY AS c(value, ordinality)
          )
        ),
        v_plan.row_offset,
        v_plan.row_take
      )
    END
  );
  EXECUTE format('SELECT r[''j'']::text FROM duckdb.query(%L) r', v_duckdb_sql) INTO v_result;
  v_result := coalesce(v_result, '[]');

  PERFORM {{ schema }}.set_response_headers('ducklake', v_snapshot);
  RETURN {{ schema }}.page_aggregate(
    v_result,
    v_plan.order_by,
    v_plan.row_offset,
    v_plan.row_take,
    v_plan.row_limit,
    v_meta.max_rows
  )::json;
END;
$$;

-- The latest snapshot of the schema, read by the function snapshot of that schema.
CREATE OR REPLACE FUNCTION {{ schema }}.snapshot(p_schema text) RETURNS bigint
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_snapshot bigint;
BEGIN
  EXECUTE format('SELECT %I.snapshot()', p_schema) INTO v_snapshot;
  RETURN v_snapshot;
END;
$$;

-- The estimate of the distinct values of one column, in DuckDB, with a HyperLogLog sketch of the datasketches
-- extension. The answer is [{"estimate": n}], or one object for each group of the group_by column.
-- A cap on the groups cuts the answer at max_rows, and then the response is partial (206) as PostgREST does.
CREATE OR REPLACE FUNCTION {{ schema }}.hll(
  p_schema text,
  p_table text,
  p_column text,
  p_group_by text,
  p_lg_k integer
) RETURNS json
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp AS $$
DECLARE
  v_meta {{ schema }}.meta;
  v_where text;
  v_pinned bigint;
  v_snapshot bigint;
  v_ducklake_only boolean;
  v_result text;
  v_rows jsonb;
  v_sql text;
BEGIN
  v_meta := {{ schema }}.require_table(p_schema, p_table, 'read_api');
  IF p_lg_k IS NULL OR p_lg_k NOT BETWEEN 4 AND 21 THEN
    RAISE EXCEPTION 'lg_k must be a whole number from 4 to 21'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_column IS NULL
    OR NOT p_column = ANY(ARRAY(SELECT jsonb_array_elements_text({{ schema }}.meta_columns(v_meta.columns, 'groupable'))))
  THEN
    RAISE EXCEPTION 'The column must be a text, whole number, date or true-or-false column of the table'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  IF p_group_by IS NOT NULL
    AND NOT p_group_by = ANY(ARRAY(SELECT jsonb_array_elements_text({{ schema }}.meta_columns(v_meta.columns, 'groupable'))))
  THEN
    RAISE EXCEPTION 'The group_by column must be a text, whole number, date or true-or-false column of the table'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  v_where := {{ schema }}.rls_where(
    p_schema, v_meta.scope_path, current_setting(v_meta.claim_setting, true), v_meta.rls_mappings, true
  );
  IF v_where IS NULL THEN
    RETURN '[]'::json;
  END IF;

  v_pinned := {{ schema }}.requested_snapshot();
  SELECT coalesce(bool_or(plan.name = 'ducklake' AND plan.use), false)
    AND NOT coalesce(bool_or(plan.name <> 'ducklake' AND plan.use), false)
  INTO v_ducklake_only
  FROM {{ schema }}.plan_sources(
    v_meta.source_table,
    ARRAY(SELECT f ->> 'name' FROM jsonb_array_elements(v_meta.fallbacks) AS f),
    v_pinned IS NOT NULL
  ) AS plan;
  IF NOT v_ducklake_only THEN
    RAISE EXCEPTION 'A fallback source serves part of this table, and the estimate reads only DuckLake'
      USING ERRCODE = 'PT409';
  END IF;

  v_snapshot := coalesce(v_pinned, {{ schema }}.snapshot(p_schema));
  EXECUTE format('SELECT %I.%I($1, $2, $3, NULL, NULL)', p_schema, p_table || '_dl_fn')
    USING v_where, v_snapshot::text, v_pinned IS NOT NULL;

  PERFORM duckdb.raw_query('LOAD datasketches');
  v_sql := format(
    'SELECT CAST(json_group_array(to_json(t)) AS VARCHAR) AS j FROM ('
    || 'SELECT %s CAST(round(datasketch_hll_estimate(datasketch_hll(%s, CAST(%s AS VARCHAR)))) AS BIGINT) AS estimate '
    || 'FROM %I%s%s LIMIT %s) t',
    CASE WHEN p_group_by IS NULL THEN '' ELSE {{ schema }}.quote_column(p_group_by) || ' AS ' || {{ schema }}.quote_column(p_group_by) || ', ' END,
    p_lg_k,
    {{ schema }}.quote_column(p_column),
    v_meta.view,
    CASE WHEN p_group_by IS NULL THEN '' ELSE ' GROUP BY ' || {{ schema }}.quote_column(p_group_by) END,
    CASE WHEN p_group_by IS NULL THEN '' ELSE ' ORDER BY ' || {{ schema }}.quote_column(p_group_by) END,
    v_meta.max_rows + 1
  );
  EXECUTE format('SELECT r[''j'']::text FROM duckdb.query(%L) r', v_sql) INTO v_result;
  v_rows := coalesce(v_result, '[]')::jsonb;

  PERFORM {{ schema }}.set_response_headers('ducklake', v_snapshot);
  IF jsonb_array_length(v_rows) > v_meta.max_rows THEN
    PERFORM set_config('response.status', '206', true);
    PERFORM set_config(
      'response.headers',
      (
        current_setting('response.headers')::jsonb
        || jsonb_build_array(jsonb_build_object('Content-Range', format('0-%s/*', v_meta.max_rows - 1)))
      )::text,
      true
    );
    RETURN (
      SELECT jsonb_agg(e.value ORDER BY e.ordinality)
      FROM jsonb_array_elements(v_rows) WITH ORDINALITY AS e(value, ordinality)
      WHERE e.ordinality <= v_meta.max_rows
    )::json;
  END IF;
  RETURN v_rows::json;
END;
$$;

-- The changed rows of a table between two snapshots. Each row holds the change and, in data, the columns of the table.
CREATE OR REPLACE FUNCTION {{ schema }}.changes(
  p_schema text,
  p_table text,
  p_start bigint,
  p_end bigint
) RETURNS TABLE (
  change_snapshot_id bigint,
  change_row_id bigint,
  change_type text,
  data jsonb
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, {{ schema }}, pg_temp AS $$
DECLARE
  v_meta {{ schema }}.meta;
  v_where text;
  v_end bigint;
  v_view text;
  v_row record;
BEGIN
  v_meta := {{ schema }}.require_table(p_schema, p_table, 'read_ducklake');
  v_where := {{ schema }}.rls_where(
    p_schema, v_meta.scope_path, current_setting(v_meta.claim_setting, true), v_meta.rls_mappings, true
  );
  IF v_where IS NULL THEN
    RETURN;
  END IF;

  PERFORM {{ schema }}.attach_ducklake(p_schema, v_meta.data_path);

  IF p_end IS NULL THEN
    EXECUTE format('SELECT %I.snapshot()', p_schema) INTO v_end;
  ELSE
    v_end := p_end;
  END IF;

  v_view := 'dl_changes_' || p_schema || '_' || p_table;
  PERFORM duckdb.raw_query(format(
    'CREATE OR REPLACE VIEW %I AS SELECT snapshot_id AS change_snapshot_id, rowid AS change_row_id, change_type, '
    || 'struct_pack(%s) AS change_data FROM %I.table_changes(%L, %s, %s) %s',
    v_view,
    (
      SELECT string_agg(
        {{ schema }}.quote_column(c.value ->> 'name') || ' := ' || {{ schema }}.quote_column(c.value ->> 'name'),
        ', ' ORDER BY c.ordinality
      )
      FROM jsonb_array_elements(v_meta.columns) WITH ORDINALITY AS c(value, ordinality)
    ),
    'dl_' || p_schema,
    p_table,
    p_start,
    v_end,
    v_where
  ));

  FOR v_row IN EXECUTE format(
    'SELECT r[''change_snapshot_id'']::bigint AS snapshot_id, r[''change_row_id'']::bigint AS row_id, '
    || 'r[''change_type'']::text AS kind, r[''change_data'']::text AS body '
    || 'FROM duckdb.query(%L) r',
    format(
      'SELECT change_snapshot_id, change_row_id, change_type, CAST(to_json(change_data) AS VARCHAR) AS change_data FROM %I',
      v_view
    )
  )
  LOOP
    change_snapshot_id := v_row.snapshot_id;
    change_row_id := v_row.row_id;
    change_type := v_row.kind;
    data := v_row.body::jsonb;
    RETURN NEXT;
  END LOOP;
END;
$$;

REVOKE ALL ON FUNCTION
  {{ schema }}.require_table(text, text, text),
  {{ schema }}.rls_where(text, text, text, jsonb, boolean),
  {{ schema }}.attach_ducklake(text, text),
  {{ schema }}.snapshot(text),
  {{ schema }}.agg(text, text),
  {{ schema }}.hll(text, text, text, text, integer),
  {{ schema }}.changes(text, text, bigint, bigint)
FROM public
