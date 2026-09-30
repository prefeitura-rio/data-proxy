{#
{
  "kind": "template",
  "description": "Create the snapshot function and two source helpers that report their arguments.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
CREATE FUNCTION {{ schema }}.ducklake_latest_snapshot() RETURNS bigint
LANGUAGE sql AS 'SELECT 7::bigint';

CREATE FUNCTION {{ schema }}.t_dl_fn(p_where text, p_arg text)
RETURNS TABLE (source text, arg1 text, arg2 text)
LANGUAGE sql AS $$
    SELECT 'ducklake', p_where, p_arg
    WHERE current_setting('test.dl_empty', true) IS DISTINCT FROM 'on'
$$;

CREATE FUNCTION {{ schema }}.t_bq_fn(p_where text, p_arg text)
RETURNS TABLE (source text, arg1 text, arg2 text)
LANGUAGE sql AS $$
    SELECT 'bigquery', p_where, p_arg
$$;
