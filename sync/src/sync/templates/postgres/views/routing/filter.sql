{#
{
  "kind": "template",
  "description": "Read and render the proxy-pushed DuckLake WHERE filter."
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

  BEGIN
    RETURN v_value::jsonb;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'x-ducklake-filter must be a JSON object' USING ERRCODE = 'PT400';
  END;
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

    operator_name := node ->> 'operator';

    IF jsonb_typeof(node -> 'value') = 'array' THEN
      IF operator_name <> 'in' THEN
        RAISE EXCEPTION 'list value for non-in operator' USING ERRCODE = 'PT400';
      END IF;
      SELECT array_agg(format('%L', value))
      INTO parts
      FROM jsonb_array_elements_text(node -> 'value') AS values(value);
      rendered := format('%I IN (%s)', column_name, array_to_string(parts, ', '));
    ELSE
      value_text := node ->> 'value';
      rendered := CASE operator_name
        WHEN 'eq'    THEN format('%I = %L', column_name, value_text)
        WHEN 'neq'   THEN format('%I <> %L', column_name, value_text)
        WHEN 'gt'    THEN format('%I > %L', column_name, value_text)
        WHEN 'gte'   THEN format('%I >= %L', column_name, value_text)
        WHEN 'lt'    THEN format('%I < %L', column_name, value_text)
        WHEN 'lte'   THEN format('%I <= %L', column_name, value_text)
        WHEN 'like'  THEN format('%I LIKE %L', column_name, replace(value_text, '*', '%'))
        WHEN 'ilike' THEN format('%I ILIKE %L', column_name, replace(value_text, '*', '%'))
        WHEN 'is'    THEN CASE value_text
          WHEN 'not_null' THEN format('%I IS NOT NULL', column_name)
          WHEN 'null'     THEN format('%I IS NULL', column_name)
          WHEN 'true'     THEN format('%I IS TRUE', column_name)
          WHEN 'false'    THEN format('%I IS FALSE', column_name)
          WHEN 'unknown'  THEN format('%I IS UNKNOWN', column_name)
          ELSE NULL
        END
        ELSE NULL
      END;
    END IF;

    IF rendered IS NULL THEN
      RAISE EXCEPTION 'unknown filter operator' USING ERRCODE = 'PT400';
    END IF;

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
