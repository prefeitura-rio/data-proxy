DROP SCHEMA IF EXISTS pushdown CASCADE;
CREATE SCHEMA pushdown;

CREATE TABLE pushdown.items_data (
    id integer PRIMARY KEY,
    name text NOT NULL,
    active boolean NOT NULL,
    owner text NOT NULL
);

INSERT INTO pushdown.items_data (id, name, active, owner)
VALUES
    (1, 'Rio', true, 'alice'),
    (2, 'Tokyo', false, 'bob'),
    (3, 'Paris', true, 'bob'),
    (4, 'Rio Grande', false, 'alice');

ALTER TABLE pushdown.items_data ENABLE ROW LEVEL SECURITY;
CREATE POLICY items_owner_policy ON pushdown.items_data
    USING (
        COALESCE(
            NULLIF(CURRENT_SETTING('request.jwt.claims', true), '')::jsonb ->> 'sub',
            owner
        ) = owner
    );

CREATE FUNCTION pushdown.render_filter_node(node jsonb, columns text[])
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    kind text;
    column_name text;
    operator_name text;
    value_text text;
    rendered text;
    item jsonb;
    parts text[];
BEGIN
    kind := node ->> 'kind';
    IF kind = 'comparison' THEN
        column_name := node ->> 'column';
        IF NOT column_name = ANY(columns) THEN
            RAISE EXCEPTION 'unknown filter column';
        END IF;
        operator_name := node ->> 'operator';
        IF jsonb_typeof(node -> 'value') = 'array' THEN
            IF operator_name <> 'in' THEN
                RAISE EXCEPTION 'list value for non-in operator';
            END IF;
            SELECT array_agg(format('%L', value))
            INTO parts
            FROM jsonb_array_elements_text(node -> 'value') AS values(value);
            rendered := format('%I IN (%s)', column_name, array_to_string(parts, ', '));
        ELSE
            value_text := node ->> 'value';
            rendered := CASE operator_name
                WHEN 'eq' THEN format('%I = %L', column_name, value_text)
                WHEN 'neq' THEN format('%I <> %L', column_name, value_text)
                WHEN 'gt' THEN format('%I > %L', column_name, value_text)
                WHEN 'gte' THEN format('%I >= %L', column_name, value_text)
                WHEN 'lt' THEN format('%I < %L', column_name, value_text)
                WHEN 'lte' THEN format('%I <= %L', column_name, value_text)
                WHEN 'like' THEN format('%I LIKE %L', column_name, replace(value_text, '*', '%'))
                WHEN 'ilike' THEN format('%I ILIKE %L', column_name, replace(value_text, '*', '%'))
                WHEN 'is' THEN CASE value_text
                    WHEN 'not_null' THEN format('%I IS NOT NULL', column_name)
                    ELSE format('%I IS %s', column_name, upper(value_text))
                END
                ELSE NULL
            END;
        END IF;
        IF rendered IS NULL THEN
            RAISE EXCEPTION 'unknown filter operator';
        END IF;
        IF COALESCE((node ->> 'negated')::boolean, false) THEN
            rendered := 'NOT (' || rendered || ')';
        END IF;
        RETURN rendered;
    END IF;

    IF kind <> 'group' THEN
        RAISE EXCEPTION 'unknown filter node';
    END IF;

    SELECT array_agg(pushdown.render_filter_node(node_value, columns))
    INTO parts
    FROM jsonb_array_elements(node -> 'items') AS nodes(node_value);
    IF parts IS NULL OR array_length(parts, 1) = 0 THEN
        RAISE EXCEPTION 'empty filter group';
    END IF;
    rendered := '(' || array_to_string(parts, ' ' || upper(node ->> 'operator') || ' ') || ')';
    IF COALESCE((node ->> 'negated')::boolean, false) THEN
        rendered := 'NOT ' || rendered;
    END IF;
    RETURN rendered;
END;
$$;

CREATE FUNCTION pushdown.render_filter(filter jsonb)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    suffix text := '';
    order_item jsonb;
    order_parts text[] := ARRAY[]::text[];
    column_name text;
    direction text;
    nulls text;
BEGIN
    IF filter ? 'where' THEN
        suffix := ' WHERE ' || pushdown.render_filter_node(
            filter -> 'where', ARRAY['id', 'name', 'active']
        );
    END IF;
    IF filter ? 'order' THEN
        FOR order_item IN SELECT value FROM jsonb_array_elements(filter -> 'order') LOOP
            column_name := order_item ->> 'column';
            IF NOT column_name = ANY(ARRAY['id', 'name', 'active']) THEN
                RAISE EXCEPTION 'unknown order column';
            END IF;
            direction := upper(order_item ->> 'direction');
            IF direction NOT IN ('ASC', 'DESC') THEN
                RAISE EXCEPTION 'unknown order direction';
            END IF;
            nulls := CASE order_item ->> 'nulls'
                WHEN 'first' THEN ' NULLS FIRST'
                WHEN 'last' THEN ' NULLS LAST'
                ELSE ''
            END;
            order_parts := array_append(order_parts, format('%I %s%s', column_name, direction, nulls));
        END LOOP;
        suffix := suffix || ' ORDER BY ' || array_to_string(order_parts, ', ');
    ELSE
        suffix := suffix || ' ORDER BY id';
    END IF;
    IF filter ? 'limit' THEN
        suffix := suffix || ' LIMIT ' || (filter ->> 'limit');
    END IF;
    IF filter ? 'offset' THEN
        suffix := suffix || ' OFFSET ' || (filter ->> 'offset');
    END IF;
    RETURN suffix;
END;
$$;

CREATE FUNCTION pushdown.items()
RETURNS TABLE (id integer, name text, active boolean)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    headers jsonb := COALESCE(NULLIF(CURRENT_SETTING('request.headers', true), ''), '{}')::jsonb;
    filter jsonb := NULLIF(
        COALESCE(headers ->> 'X-DuckLake-Filter', headers ->> 'x-ducklake-filter'),
        ''
    )::jsonb;
    query text := 'SELECT id, name, active FROM pushdown.items_data';
BEGIN
    IF filter IS NOT NULL THEN
        query := query || pushdown.render_filter(filter);
    ELSE
        query := query || ' ORDER BY id';
    END IF;
    RETURN QUERY EXECUTE query;
END;
$$;

GRANT USAGE ON SCHEMA pushdown TO anon;
GRANT EXECUTE ON FUNCTION pushdown.items() TO anon;
GRANT SELECT ON pushdown.items_data TO anon;
