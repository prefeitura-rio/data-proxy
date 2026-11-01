{#
{
  "kind": "template",
  "description": "Read the aggregate of a request into the parts of the DuckDB query that answers it. The function gives no row for a request that DuckDB can't answer in the same way as the table function.",
  "inputs": {
    "schema": "PostgreSQL schema that holds the routing functions."
  }
}
#}
-- The parts of the DuckDB query for the aggregate of a request. p_columns says what
-- each column allows: the arrays names and kinds (they have the same order), and the
-- arrays group, count, sum, avg, min and max with the names of the allowed columns.
-- The function gives no row when a name, a function or a type isn't allowed, and then
-- the proxy sends the request to the table function.
CREATE OR REPLACE FUNCTION {{ schema }}.plan_aggregate(
  p_aggregate jsonb,
  p_columns jsonb,
  p_max_rows bigint
) RETURNS TABLE (
  select_list text,
  group_by text,
  order_by text,
  row_offset bigint,
  row_take bigint,
  row_limit bigint
)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_names text[] := ARRAY(SELECT jsonb_array_elements_text(p_columns -> 'names'));
  v_kinds text[] := ARRAY(SELECT jsonb_array_elements_text(p_columns -> 'kinds'));
  v_item jsonb;
  v_column text;
  v_alias text;
  v_type text;
  v_expression text;
  v_select text[] := ARRAY[]::text[];
  v_groups integer := 0;
  v_order text[] := ARRAY[]::text[];
  v_offset bigint;
  v_limit bigint;
  v_take bigint;
BEGIN
  IF jsonb_typeof(p_aggregate -> 'group') = 'array' THEN
    FOR v_item IN SELECT jsonb_array_elements(p_aggregate -> 'group') LOOP
      v_column := v_item ->> 'column';
      v_alias := v_item ->> 'alias';
      IF v_alias IS NULL
        OR NOT v_column = ANY(ARRAY(SELECT jsonb_array_elements_text(p_columns -> 'group')))
      THEN
        RETURN;
      END IF;
      v_select := v_select || format('%I AS %I', v_column, v_alias);
      v_groups := v_groups + 1;
    END LOOP;
  END IF;

  -- PostgreSQL orders and cuts the rows of the groups, so the text order follows its
  -- collation. The order names a column of the group, and the column has a type that
  -- PostgreSQL sorts from its text.
  FOR v_item IN SELECT jsonb_array_elements(p_aggregate -> 'order') LOOP
    v_column := v_item ->> 'column';
    v_alias := (
      SELECT entry ->> 'alias'
      FROM jsonb_array_elements(coalesce(p_aggregate -> 'group', '[]'::jsonb)) AS entry
      WHERE entry ->> 'column' = v_column
      LIMIT 1
    );
    v_type := v_kinds[array_position(v_names, v_column)];
    IF v_alias IS NULL
      OR v_type IS NULL
      OR v_type NOT IN ('text', 'bigint', 'date', 'boolean')
    THEN
      RETURN;
    END IF;
    v_order := v_order || format(
      '(t.elem ->> %L)%s %s%s',
      v_alias,
      CASE WHEN v_type = 'text' THEN '' ELSE '::' || v_type END,
      CASE WHEN (v_item ->> 'descending')::boolean THEN 'DESC' ELSE 'ASC' END,
      CASE v_item ->> 'nulls'
        WHEN 'first' THEN ' NULLS FIRST'
        WHEN 'last' THEN ' NULLS LAST'
        ELSE ''
      END
    );
  END LOOP;
  v_offset := coalesce((p_aggregate ->> 'offset')::bigint, 0);
  v_limit := (p_aggregate ->> 'limit')::bigint;
  v_take := least(coalesce(v_limit, p_max_rows), p_max_rows);

  FOR v_item IN SELECT jsonb_array_elements(p_aggregate -> 'items') LOOP
    v_column := v_item ->> 'column';
    v_expression := CASE
      WHEN v_item ->> 'fn' = 'count' AND v_column IS NULL THEN 'count(*)'
      WHEN v_item ->> 'fn' IN ('count', 'sum', 'avg', 'min', 'max')
        AND v_column = ANY(
          ARRAY(SELECT jsonb_array_elements_text(p_columns -> (v_item ->> 'fn')))
        )
        THEN format('%s(%I)', v_item ->> 'fn', v_column)
    END;
    IF v_expression IS NULL OR (v_item ->> 'alias') IS NULL THEN
      RETURN;
    END IF;
    v_select := v_select || format('%s AS %I', v_expression, v_item ->> 'alias');
  END LOOP;

  RETURN QUERY SELECT
    array_to_string(v_select, ', '),
    CASE
      WHEN v_groups > 0 THEN ' GROUP BY ' || (
        SELECT string_agg(ordinal::text, ', ') FROM generate_series(1, v_groups) AS ordinal
      )
      ELSE ''
    END,
    CASE
      WHEN cardinality(v_order) > 0 THEN 'ORDER BY ' || array_to_string(v_order, ', ')
      ELSE ''
    END,
    v_offset,
    v_take,
    v_limit;
END;
$$;
