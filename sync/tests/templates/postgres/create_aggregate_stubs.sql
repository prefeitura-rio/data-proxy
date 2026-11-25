{#
{
  "kind": "template",
  "description": "Create the snapshot function and a DuckLake helper whose view holds six rows.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
{#
The view applies the WHERE clause that it receives, as the real helper does.
#}
CREATE FUNCTION {{ schema }}.snapshot() RETURNS bigint
LANGUAGE sql AS 'SELECT 7::bigint';

CREATE FUNCTION {{ schema }}.t_dl_fn(
  p_where text, p_arg text, p_validate_snapshot boolean, p_columns text[], p_tail text
)
RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM duckdb.raw_query(
    'CREATE OR REPLACE VIEW dl_t AS SELECT * FROM (VALUES '
    || '(''u1'', ''a'', CAST(1 AS BIGINT), CAST(''2026-01-01'' AS DATE), true, ''x'', CAST(0.5 AS DOUBLE), ''Zeta'', CAST(1.10 AS DECIMAL(18,2)), CAST(''2026-01-01 00:00:00.000001'' AS TIMESTAMP)), '
    || '(''u1'', ''a'', CAST(2 AS BIGINT), CAST(''2026-02-01'' AS DATE), false, ''x'', CAST(1.5 AS DOUBLE), ''alpha'', CAST(2.20 AS DECIMAL(18,2)), CAST(''2026-01-01 00:00:10'' AS TIMESTAMP)), '
    || '(''u1'', ''b'', CAST(4 AS BIGINT), CAST(''2026-03-01'' AS DATE), true, ''x'', CAST(2.0 AS DOUBLE), ''Beta'', CAST(3.30 AS DECIMAL(18,2)), CAST(''2026-01-01 00:00:20.123456'' AS TIMESTAMP)), '
    || '(''u2'', ''a'', CAST(8 AS BIGINT), CAST(''2026-04-01'' AS DATE), true, ''x'', CAST(4.0 AS DOUBLE), ''alpha'', CAST(4.40 AS DECIMAL(18,2)), CAST(''2026-01-01 00:00:30'' AS TIMESTAMP)), '
    || '(''u2'', ''b'', CAST(NULL AS BIGINT), CAST(''2026-05-01'' AS DATE), false, ''x'', CAST(NULL AS DOUBLE), ''zeta'', CAST(NULL AS DECIMAL(18,2)), CAST(NULL AS TIMESTAMP)), '
    || '(''u3'', ''b'', CAST(32 AS BIGINT), CAST(''2026-06-01'' AS DATE), true, ''x'', CAST(6.0 AS DOUBLE), ''Alpha'', CAST(6.60 AS DECIMAL(18,2)), CAST(''2026-01-01 00:00:40'' AS TIMESTAMP))'
    || ') AS v(unit_id, grp, qty, day, flag, tags, ratio, label, amount, stamp) ' || coalesce(p_where, '')
  );
END;
$$;

CREATE FUNCTION {{ schema }}.t_bq_fn(p_where text, p_arg text)
RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  NULL;
END;
$$;
