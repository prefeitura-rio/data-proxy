{#
{
  "kind": "template",
  "description": "Response header and WHERE clause helper functions."
}
#}
-- noqa: disable=PRS,LT05

CREATE OR REPLACE FUNCTION {{ schema }}.source_label(p_sources text[])
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT string_agg(source, '+' ORDER BY ord)
  FROM unnest(p_sources) WITH ORDINALITY AS t(source, ord)
  WHERE source IS NOT NULL
$$;

CREATE OR REPLACE FUNCTION {{ schema }}.and_where(p_where text, p_condition text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN p_condition IS NULL OR p_condition = 'TRUE' THEN p_where
    WHEN p_where = '' THEN 'WHERE (' || p_condition || ')'
    ELSE p_where || ' AND (' || p_condition || ')'
  END
$$;

CREATE OR REPLACE FUNCTION {{ schema }}.set_response_headers(p_source text, p_snapshot bigint)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  v_headers json[] := ARRAY[]::json[];
BEGIN
  IF p_source IS NOT NULL THEN
    v_headers := v_headers || json_build_object('X-Source', p_source);
  END IF;

  IF p_snapshot IS NOT NULL THEN
    v_headers := v_headers || json_build_object('X-DuckLake-Snapshot', p_snapshot::text);
  END IF;

  PERFORM set_config('response.headers', to_json(v_headers)::text, true);
END;
$$;
