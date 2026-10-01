{#
{
  "kind": "template",
  "description": "Create the snapshot function and two void source helpers.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
CREATE FUNCTION {{ schema }}.ducklake_latest_snapshot() RETURNS bigint
LANGUAGE sql AS 'SELECT 7::bigint';

CREATE FUNCTION {{ schema }}.t_dl_fn(p_where text, p_arg text)
RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('test.dl_empty', true) = 'on' THEN
    PERFORM duckdb.raw_query(
      'CREATE OR REPLACE VIEW ducklake_t AS ' ||
      'SELECT ''ducklake''::VARCHAR AS source, ' ||
      quote_literal(p_where) || ' AS arg1, ' ||
      quote_literal(p_arg) || ' AS arg2 WHERE false'
    );
  ELSE
    PERFORM duckdb.raw_query(
      'CREATE OR REPLACE VIEW ducklake_t AS ' ||
      'SELECT ''ducklake''::VARCHAR AS source, ' ||
      quote_literal(p_where) || ' AS arg1, ' ||
      quote_literal(p_arg) || ' AS arg2'
    );
  END IF;
END;
$$;

CREATE FUNCTION {{ schema }}.t_bq_fn(p_where text, p_arg text)
RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM duckdb.raw_query(
    'CREATE OR REPLACE VIEW source_t AS ' ||
    'SELECT ''bigquery''::VARCHAR AS source, ' ||
    quote_literal(p_where) || ' AS arg1, ' ||
    quote_literal(p_arg) || ' AS arg2'
  );
END;
$$;
