{#
{
  "kind": "template",
  "description": "Read and render what the proxy pushes in the X-DuckLake-Filter header: the WHERE filter, the columns, and the ORDER BY with the LIMIT."
}
#}
-- noqa: disable=PRS,LT05

CREATE OR REPLACE FUNCTION {{ schema }}.request_filter() RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_value text;
BEGIN
  SELECT header.value INTO v_value
  FROM json_each_text(
    coalesce(nullif(current_setting('request.headers', true), '')::json, '{}'::json)
  ) AS header
  WHERE lower(header.key) = 'x-ducklake-filter';

  IF NOT FOUND OR v_value IS NULL THEN
    RETURN NULL;
  END IF;

  IF NOT pg_input_is_valid(v_value, 'jsonb') THEN
    RAISE EXCEPTION 'x-ducklake-filter must be a JSON object' USING ERRCODE = 'PT400';
  END IF;

  RETURN v_value::jsonb;
END;
$$;

CREATE OR REPLACE FUNCTION {{ schema }}.render_filter_node(
  node jsonb,
  columns text[]
) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE
  kind text;
  column_name text;
  quoted_column text;
  operator_name text;
  value_text text;
  rendered text;
  parts text[];
BEGIN
  kind := node ->> 'kind';

  IF kind = 'comparison' THEN
    column_name := node ->> 'column';
    IF NOT column_name = ANY(columns) THEN
      RAISE EXCEPTION 'unknown filter column' USING ERRCODE = 'PT400';
    END IF;

    quoted_column := '"' || replace(column_name, '"', '""') || '"';
    operator_name := node ->> 'operator';

    IF jsonb_typeof(node -> 'value') = 'array' THEN
      IF operator_name <> 'in' THEN
        RAISE EXCEPTION 'list value for non-in operator' USING ERRCODE = 'PT400';
      END IF;
      SELECT array_agg(format('%L', value))
      INTO parts
      FROM jsonb_array_elements_text(node -> 'value') AS values(value);
      rendered := format('%s IN (%s)', quoted_column, array_to_string(parts, ', '));
    ELSE
      value_text := node ->> 'value';
      rendered := CASE operator_name
        WHEN 'eq'    THEN format('%s = %L', quoted_column, value_text)
        WHEN 'neq'   THEN format('%s <> %L', quoted_column, value_text)
        WHEN 'gt'    THEN format('%s > %L', quoted_column, value_text)
        WHEN 'gte'   THEN format('%s >= %L', quoted_column, value_text)
        WHEN 'lt'    THEN format('%s < %L', quoted_column, value_text)
        WHEN 'lte'   THEN format('%s <= %L', quoted_column, value_text)
        WHEN 'like'  THEN format('%s LIKE %L', quoted_column, replace(value_text, '*', '%'))
        WHEN 'ilike' THEN format('%s ILIKE %L', quoted_column, replace(value_text, '*', '%'))
        WHEN 'is'    THEN CASE value_text
          WHEN 'not_null' THEN format('%s IS NOT NULL', quoted_column)
          WHEN 'null'     THEN format('%s IS NULL', quoted_column)
          WHEN 'true'     THEN format('%s IS TRUE', quoted_column)
          WHEN 'false'    THEN format('%s IS FALSE', quoted_column)
          WHEN 'unknown'  THEN format('%s IS UNKNOWN', quoted_column)
          ELSE NULL
        END
        ELSE NULL
      END;
    END IF;

    IF rendered IS NULL THEN
      RAISE EXCEPTION 'unknown filter operator' USING ERRCODE = 'PT400';
    END IF;

    rendered := '(' || rendered || ')';

    IF COALESCE((node ->> 'negated')::boolean, false) THEN
      rendered := 'NOT (' || rendered || ')';
    END IF;

    RETURN rendered;
  END IF;

  IF kind <> 'group' THEN
    RAISE EXCEPTION 'unknown filter node' USING ERRCODE = 'PT400';
  END IF;

  IF (node ->> 'operator') NOT IN ('and', 'or') THEN
    RAISE EXCEPTION 'unknown logical operator' USING ERRCODE = 'PT400';
  END IF;

  SELECT array_agg({{ schema }}.render_filter_node(item, columns))
  INTO parts
  FROM jsonb_array_elements(node -> 'items') AS nodes(item);

  IF parts IS NULL OR array_length(parts, 1) = 0 THEN
    RAISE EXCEPTION 'empty filter group' USING ERRCODE = 'PT400';
  END IF;

  rendered := '(' || array_to_string(parts, ' ' || upper(node ->> 'operator') || ' ') || ')';

  IF COALESCE((node ->> 'negated')::boolean, false) THEN
    rendered := 'NOT ' || rendered;
  END IF;

  RETURN rendered;
END;
$$;

CREATE OR REPLACE FUNCTION {{ schema }}.render_dl_where(
  p_filter jsonb,
  p_columns text[]
) RETURNS text
LANGUAGE plpgsql STABLE AS $$
BEGIN
  IF p_filter IS NULL OR NOT p_filter ? 'where' THEN
    RETURN NULL;
  END IF;

  RETURN {{ schema }}.render_filter_node(p_filter -> 'where', p_columns);
END;
$$;

-- The columns that the request reads. NULL means every column.
-- A name that is not a column is dropped, so PostgREST answers with its own error for it.
CREATE OR REPLACE FUNCTION {{ schema }}.render_dl_columns(
  p_filter jsonb,
  p_columns text[]
) RETURNS text[]
LANGUAGE plpgsql STABLE AS $$
BEGIN
  IF p_filter IS NULL OR NOT p_filter ? 'columns' THEN
    RETURN NULL;
  END IF;

  IF jsonb_typeof(p_filter -> 'columns') <> 'array' THEN
    RAISE EXCEPTION 'filter columns must be a list' USING ERRCODE = 'PT400';
  END IF;

  RETURN coalesce(
    (
      SELECT array_agg(item.name ORDER BY item.position)
      FROM jsonb_array_elements_text(p_filter -> 'columns') WITH ORDINALITY AS item(name, position)
      WHERE item.name = ANY(p_columns)
    ),
    ARRAY[]::text[]
  );
END;
$$;

-- The ORDER BY and LIMIT that DuckDB can run, or NULL when PostgREST must read every row.
-- The text order of DuckDB is by bytes and the order of PostgreSQL follows the collation, and every
-- column that isn't a bigint, a date, or a boolean reaches PostgreSQL as text. DuckDB then picks other
-- rows for a limit, so the order is pushed only when every order column is in p_sortable.
-- PostgreSQL puts the nulls last for ASC and first for DESC, and DuckDB needs them in the text.
CREATE OR REPLACE FUNCTION {{ schema }}.render_dl_tail(
  p_filter jsonb,
  p_sortable text[]
) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_limit bigint;
  v_order text;
BEGIN
  IF p_filter IS NULL OR NOT p_filter ? 'limit' THEN
    RETURN NULL;
  END IF;

  v_limit := (p_filter ->> 'limit')::bigint;
  IF v_limit < 0 THEN
    RAISE EXCEPTION 'filter limit must not be negative' USING ERRCODE = 'PT400';
  END IF;

  IF p_filter ? 'order' THEN
    IF EXISTS (
      SELECT FROM jsonb_array_elements(p_filter -> 'order') AS item(entry)
      WHERE NOT (item.entry ->> 'column') = ANY(p_sortable)
    ) THEN
      RETURN NULL;
    END IF;

    SELECT 'ORDER BY ' || string_agg(
      '"' || replace(item.entry ->> 'column', '"', '""') || '" '
      || CASE WHEN (item.entry ->> 'descending')::boolean THEN 'DESC' ELSE 'ASC' END
      || ' NULLS '
      || upper(coalesce(
        item.entry ->> 'nulls',
        CASE WHEN (item.entry ->> 'descending')::boolean THEN 'first' ELSE 'last' END
      )),
      ', ' ORDER BY item.position
    ) || ' '
    INTO v_order
    FROM jsonb_array_elements(p_filter -> 'order') WITH ORDINALITY AS item(entry, position);
  END IF;

  RETURN coalesce(v_order, '') || 'LIMIT ' || v_limit;
END;
$$;

-- The projection of the DuckLake view. A column that the request skips is a NULL, so DuckDB never reads it.
-- A typed NULL, because DuckDB can't cast an untyped NULL to a date.
CREATE OR REPLACE FUNCTION {{ schema }}.project_columns(
  p_names text[],
  p_wanted text[]
) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT string_agg(
    CASE
      WHEN p_wanted IS NULL OR item.name = ANY(p_wanted)
        THEN '"' || replace(item.name, '"', '""') || '"'
      ELSE 'CAST(NULL AS VARCHAR) AS "' || replace(item.name, '"', '""') || '"'
    END,
    ', ' ORDER BY item.position
  )
  FROM unnest(p_names) WITH ORDINALITY AS item(name, position)
$$;
