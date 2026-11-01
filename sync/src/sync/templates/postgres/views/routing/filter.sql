{#
{
  "kind": "template",
  "description": "Read and render what the proxy pushes in the X-DuckLake-Filter header: the WHERE filter, the columns, and the ORDER BY with the LIMIT."
}
#}
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

-- One comparison as a DuckDB condition, for example ("id" = '1').
CREATE OR REPLACE FUNCTION {{ schema }}.render_comparison(
  p_node jsonb,
  p_columns text[]
) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_column text := p_node ->> 'column';
  v_operator text := p_node ->> 'operator';
  v_value text := p_node ->> 'value';
  v_quoted text;
  v_comparator text;
  v_condition text;
BEGIN
  IF NOT v_column = ANY(p_columns) THEN
    RAISE EXCEPTION 'unknown filter column' USING ERRCODE = 'PT400';
  END IF;

  v_quoted := {{ schema }}.quote_column(v_column);
  v_comparator := CASE v_operator
    WHEN 'eq' THEN '='
    WHEN 'neq' THEN '<>'
    WHEN 'gt' THEN '>'
    WHEN 'gte' THEN '>='
    WHEN 'lt' THEN '<'
    WHEN 'lte' THEN '<='
  END;

  IF jsonb_typeof(p_node -> 'value') = 'array' THEN
    IF v_operator <> 'in' THEN
      RAISE EXCEPTION 'list value for non-in operator' USING ERRCODE = 'PT400';
    END IF;
    v_condition := format(
      '%s IN (%s)',
      v_quoted,
      (
        SELECT string_agg(format('%L', item.value), ', ')
        FROM jsonb_array_elements_text(p_node -> 'value') AS item(value)
      )
    );
  ELSIF v_comparator IS NOT NULL THEN
    v_condition := format('%s %s %L', v_quoted, v_comparator, v_value);
  ELSIF v_operator IN ('like', 'ilike') THEN
    v_condition := format(
      '%s %s %L', v_quoted, upper(v_operator), replace(v_value, '*', '%')
    );
  ELSIF v_operator = 'is' THEN
    v_condition := v_quoted || CASE v_value
      WHEN 'not_null' THEN ' IS NOT NULL'
      WHEN 'null' THEN ' IS NULL'
      WHEN 'true' THEN ' IS TRUE'
      WHEN 'false' THEN ' IS FALSE'
      WHEN 'unknown' THEN ' IS UNKNOWN'
    END;
  END IF;

  IF v_condition IS NULL THEN
    RAISE EXCEPTION 'unknown filter operator' USING ERRCODE = 'PT400';
  END IF;

  v_condition := '(' || v_condition || ')';
  IF coalesce((p_node ->> 'negated')::boolean, false) THEN
    RETURN 'NOT (' || v_condition || ')';
  END IF;
  RETURN v_condition;
END;
$$;

-- One group of conditions joined by AND or OR, for example
-- (("a" = '1') OR ("b" = '2')).
CREATE OR REPLACE FUNCTION {{ schema }}.render_group(
  p_node jsonb,
  p_columns text[]
) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_parts text[];
  v_condition text;
BEGIN
  IF (p_node ->> 'operator') NOT IN ('and', 'or') THEN
    RAISE EXCEPTION 'unknown logical operator' USING ERRCODE = 'PT400';
  END IF;

  SELECT array_agg({{ schema }}.render_filter_node(item.node, p_columns))
  INTO v_parts
  FROM jsonb_array_elements(p_node -> 'items') AS item(node);

  IF v_parts IS NULL OR array_length(v_parts, 1) = 0 THEN
    RAISE EXCEPTION 'empty filter group' USING ERRCODE = 'PT400';
  END IF;

  v_condition := '('
    || array_to_string(v_parts, ' ' || upper(p_node ->> 'operator') || ' ')
    || ')';
  IF coalesce((p_node ->> 'negated')::boolean, false) THEN
    RETURN 'NOT ' || v_condition;
  END IF;
  RETURN v_condition;
END;
$$;

-- One node of the filter tree as a DuckDB condition: a comparison or a group.
-- The function had the parameters node and columns.
-- CREATE OR REPLACE can't rename them, so the old function goes first.
DROP FUNCTION IF EXISTS {{ schema }}.render_filter_node(jsonb, text[]);
CREATE FUNCTION {{ schema }}.render_filter_node(
  p_node jsonb,
  p_columns text[]
) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_kind text := p_node ->> 'kind';
BEGIN
  IF v_kind = 'comparison' THEN
    RETURN {{ schema }}.render_comparison(p_node, p_columns);
  ELSIF v_kind = 'group' THEN
    RETURN {{ schema }}.render_group(p_node, p_columns);
  END IF;
  RAISE EXCEPTION 'unknown filter node' USING ERRCODE = 'PT400';
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
-- A name that is not a column is dropped,
-- so PostgREST answers with its own error for it.
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

-- The ORDER BY and LIMIT that DuckDB can run, or NULL when PostgREST must read
-- every row.
-- DuckDB orders text by bytes and PostgreSQL follows the collation. Every column
-- that isn't a bigint, a date or a boolean reaches PostgreSQL as text. DuckDB
-- would then pick other rows for a limit, so the order is pushed only when every
-- order column is in p_sortable.
-- PostgreSQL puts the nulls last for ASC and first for DESC, and DuckDB needs
-- them in the text.
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
      {{ schema }}.quote_column(item.entry ->> 'column') || ' '
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

-- The projection of the DuckLake view. A column that the request skips is a NULL,
-- so DuckDB never reads it.
-- A typed NULL, because DuckDB can't cast an untyped NULL to a date.
CREATE OR REPLACE FUNCTION {{ schema }}.project_columns(
  p_names text[],
  p_wanted text[]
) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT string_agg(
    CASE
      WHEN p_wanted IS NULL OR item.name = ANY(p_wanted)
        THEN {{ schema }}.quote_column(item.name)
      ELSE 'CAST(NULL AS VARCHAR) AS ' || {{ schema }}.quote_column(item.name)
    END,
    ', ' ORDER BY item.position
  )
  FROM unnest(p_names) WITH ORDINALITY AS item(name, position)
$$;
