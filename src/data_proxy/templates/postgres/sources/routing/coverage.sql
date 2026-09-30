{#
{
  "kind": "template",
  "description": "Coverage predicate functions for DuckLake and fallback sources."
}
#}
-- noqa: disable=PRS,LT05

CREATE OR REPLACE FUNCTION {{ schema }}.sql_literal(p_value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE jsonb_typeof(p_value)
    WHEN 'number' THEN p_value #>> '{}'
    ELSE quote_literal(p_value #>> '{}')
  END
$$;

CREATE OR REPLACE FUNCTION {{ schema }}.selection_condition(p_selection jsonb) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p_selection ->> 'type'
    WHEN 'remainder' THEN format(
      '(%1$s IS NULL OR %1$s < %2$s OR %1$s >= %3$s)',
      '"' || replace(p_selection ->> 'column', '"', '""') || '"',
      {{ schema }}.sql_literal(p_selection -> 'start'),
      {{ schema }}.sql_literal(p_selection -> 'end')
    )
    ELSE format(
      '(%1$s >= %2$s AND %1$s < %3$s)',
      '"' || replace(p_selection ->> 'column', '"', '""') || '"',
      {{ schema }}.sql_literal(p_selection -> 'lower'),
      {{ schema }}.sql_literal(p_selection -> 'upper')
    )
  END
$$;

CREATE OR REPLACE FUNCTION {{ schema }}.covered_by_ducklake(p_table text) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_state jsonb;
BEGIN
  SELECT s.state INTO v_state FROM {{ schema }}.state s WHERE s.table_name = p_table;

  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  IF jsonb_typeof(v_state -> 'partitions') IS DISTINCT FROM 'object' THEN
    RETURN 'TRUE';
  END IF;

  RETURN coalesce(
    (
      SELECT string_agg({{ schema }}.selection_condition(p.value -> 'selection'), ' OR ' ORDER BY p.key)
      FROM jsonb_each(v_state -> 'partitions') AS p
    ),
    'FALSE'
  );
END;
$$;

CREATE OR REPLACE FUNCTION {{ schema }}.covered_by_fallback(p_table text) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN covered.condition IS NULL THEN 'TRUE'
    WHEN covered.condition = 'TRUE' THEN NULL
    ELSE 'NOT (' || covered.condition || ')'
  END
  FROM (SELECT {{ schema }}.covered_by_ducklake(p_table) AS condition) AS covered
$$;
