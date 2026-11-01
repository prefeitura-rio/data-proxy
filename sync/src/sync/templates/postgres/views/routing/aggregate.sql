{#
{
  "kind": "template",
  "description": "Read the aggregate of a request into the parts of the DuckDB query that answers it, and cut the groups that DuckDB gave into the page that the request asks for.",
  "inputs": {
    "schema": "PostgreSQL schema that holds the routing functions."
  }
}
#}
{#
The plan functions give NULL, or no row, for a request that DuckDB can't answer in the same way
as the table function. The proxy then sends the request to the table function.
p_columns says what each column allows: the arrays names and kinds (they have the same order),
and the arrays group, count, sum, avg, min and max with the names of the allowed columns.
#}

-- The select expressions of the group columns, for example "grp" AS "grp".
-- An empty array for a request without a group, and NULL for a column that can't group.
CREATE OR REPLACE FUNCTION {{ schema }}.plan_aggregate_group(
  p_aggregate jsonb,
  p_allowed jsonb
) RETURNS text[]
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_allowed text[] := ARRAY(SELECT jsonb_array_elements_text(p_allowed));
  v_select text[] := ARRAY[]::text[];
  v_item jsonb;
BEGIN
  IF jsonb_typeof(p_aggregate -> 'group') <> 'array' THEN
    RETURN v_select;
  END IF;

  FOR v_item IN SELECT jsonb_array_elements(p_aggregate -> 'group') LOOP
    IF (v_item ->> 'alias') IS NULL OR NOT (v_item ->> 'column') = ANY(v_allowed) THEN
      RETURN NULL;
    END IF;
    v_select := v_select || format('%I AS %I', v_item ->> 'column', v_item ->> 'alias');
  END LOOP;
  RETURN v_select;
END;
$$;

-- The ORDER BY expressions over the groups, for example (t.elem ->> 'grp') DESC.
-- PostgreSQL orders and cuts the rows of the groups, so the text order follows its
-- collation. The order names a column of the group, and the column has a type that
-- PostgreSQL sorts from its text. NULL for an order that breaks one of these rules.
CREATE OR REPLACE FUNCTION {{ schema }}.plan_aggregate_order(
  p_aggregate jsonb,
  p_columns jsonb
) RETURNS text[]
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_names text[] := ARRAY(SELECT jsonb_array_elements_text(p_columns -> 'names'));
  v_kinds text[] := ARRAY(SELECT jsonb_array_elements_text(p_columns -> 'kinds'));
  v_order text[] := ARRAY[]::text[];
  v_item jsonb;
  v_alias text;
  v_type text;
BEGIN
  FOR v_item IN SELECT jsonb_array_elements(p_aggregate -> 'order') LOOP
    v_alias := (
      SELECT entry ->> 'alias'
      FROM jsonb_array_elements(coalesce(p_aggregate -> 'group', '[]'::jsonb)) AS entry
      WHERE entry ->> 'column' = v_item ->> 'column'
      LIMIT 1
    );
    v_type := v_kinds[array_position(v_names, v_item ->> 'column')];
    IF v_alias IS NULL
      OR v_type IS NULL
      OR v_type NOT IN ('text', 'bigint', 'date', 'boolean')
    THEN
      RETURN NULL;
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
  RETURN v_order;
END;
$$;

-- The select expressions of the aggregate items, for example sum("qty") AS "n".
-- NULL for an item with an unknown function, a column that the function doesn't allow,
-- or no alias.
CREATE OR REPLACE FUNCTION {{ schema }}.plan_aggregate_items(
  p_aggregate jsonb,
  p_columns jsonb
) RETURNS text[]
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_select text[] := ARRAY[]::text[];
  v_item jsonb;
  v_function text;
  v_column text;
  v_expression text;
BEGIN
  FOR v_item IN SELECT jsonb_array_elements(p_aggregate -> 'items') LOOP
    v_function := v_item ->> 'fn';
    v_column := v_item ->> 'column';
    v_expression := CASE
      WHEN v_function = 'count' AND v_column IS NULL THEN 'count(*)'
      WHEN v_function IN ('count', 'sum', 'avg', 'min', 'max')
        AND v_column = ANY(
          ARRAY(SELECT jsonb_array_elements_text(p_columns -> v_function))
        )
        THEN format('%s(%I)', v_function, v_column)
    END;
    IF v_expression IS NULL OR (v_item ->> 'alias') IS NULL THEN
      RETURN NULL;
    END IF;
    v_select := v_select || format('%s AS %I', v_expression, v_item ->> 'alias');
  END LOOP;
  RETURN v_select;
END;
$$;

-- The parts of the DuckDB query for the aggregate of a request.
-- It gives no row when the group, the order or an item isn't allowed.
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
  v_group text[] := {{ schema }}.plan_aggregate_group(p_aggregate, p_columns -> 'group');
  v_order text[];
  v_items text[];
  v_limit bigint;
BEGIN
  IF v_group IS NULL THEN
    RETURN;
  END IF;

  v_order := {{ schema }}.plan_aggregate_order(p_aggregate, p_columns);
  IF v_order IS NULL THEN
    RETURN;
  END IF;

  v_limit := (p_aggregate ->> 'limit')::bigint;
  v_items := {{ schema }}.plan_aggregate_items(p_aggregate, p_columns);
  IF v_items IS NULL THEN
    RETURN;
  END IF;

  RETURN QUERY SELECT
    array_to_string(v_group || v_items, ', '),
    CASE
      WHEN cardinality(v_group) > 0 THEN ' GROUP BY ' || (
        SELECT string_agg(ordinal::text, ', ')
        FROM generate_series(1, cardinality(v_group)) AS ordinal
      )
      ELSE ''
    END,
    CASE
      WHEN cardinality(v_order) > 0 THEN 'ORDER BY ' || array_to_string(v_order, ', ')
      ELSE ''
    END,
    coalesce((p_aggregate ->> 'offset')::bigint, 0),
    least(coalesce(v_limit, p_max_rows), p_max_rows),
    v_limit;
END;
$$;

-- The page of the groups that the request asks for: skip the offset and keep the take.
-- One row more than the take shows that the cap cut the answer, and then the response
-- is partial (206) with a Content-Range header, as PostgREST does.
CREATE OR REPLACE FUNCTION {{ schema }}.page_aggregate(
  p_groups text,
  p_order_by text,
  p_offset bigint,
  p_take bigint,
  p_limit bigint,
  p_max_rows bigint
) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
  v_page text;
  v_returned bigint;
BEGIN
  EXECUTE format(
    'SELECT coalesce(json_agg(s.elem ORDER BY s.rn) FILTER (WHERE s.rn <= %s), ''[]''::json)::text, count(*) '
    || 'FROM (SELECT t.elem, row_number() OVER (%s) AS rn FROM json_array_elements($1::json) AS t(elem)) s '
    || 'WHERE s.rn > %s AND s.rn <= %s',
    p_offset + p_take,
    p_order_by,
    p_offset,
    p_offset + p_take + 1
  ) INTO v_page, v_returned USING p_groups;

  IF v_returned > p_take AND (p_limit IS NULL OR p_limit > p_max_rows) THEN
    PERFORM set_config('response.status', '206', true);
    PERFORM set_config(
      'response.headers',
      (
        current_setting('response.headers')::jsonb
        || jsonb_build_array(jsonb_build_object(
          'Content-Range',
          format('%s-%s/*', p_offset, p_offset + p_take - 1)
        ))
      )::text,
      true
    );
  END IF;
  RETURN v_page;
END;
$$;
