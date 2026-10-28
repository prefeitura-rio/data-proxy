{#
{
  "kind": "template",
  "description": "Create the snapshot function and a DuckLake helper whose view holds six rows with a unit, a group, a whole number, a date, a flag, a decimal number and a label. The view applies the WHERE clause that it receives, as the real helper does.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
CREATE FUNCTION {{ schema }}.dl_latest_snapshot() RETURNS bigint
LANGUAGE sql AS 'SELECT 7::bigint';

CREATE FUNCTION {{ schema }}.t_dl_fn(
  p_where text, p_arg text, p_validate_snapshot boolean, p_columns text[], p_tail text
)
RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM duckdb.raw_query(
    'CREATE OR REPLACE VIEW dl_t AS SELECT * FROM (VALUES '
    || '(''u1'', ''a'', CAST(1 AS BIGINT), CAST(''2026-01-01'' AS DATE), true, ''x'', CAST(0.5 AS DOUBLE), ''Zeta''), '
    || '(''u1'', ''a'', CAST(2 AS BIGINT), CAST(''2026-02-01'' AS DATE), false, ''x'', CAST(1.5 AS DOUBLE), ''alpha''), '
    || '(''u1'', ''b'', CAST(4 AS BIGINT), CAST(''2026-03-01'' AS DATE), true, ''x'', CAST(2.0 AS DOUBLE), ''Beta''), '
    || '(''u2'', ''a'', CAST(8 AS BIGINT), CAST(''2026-04-01'' AS DATE), true, ''x'', CAST(4.0 AS DOUBLE), ''alpha''), '
    || '(''u2'', ''b'', CAST(NULL AS BIGINT), CAST(''2026-05-01'' AS DATE), false, ''x'', CAST(NULL AS DOUBLE), ''zeta''), '
    || '(''u3'', ''b'', CAST(32 AS BIGINT), CAST(''2026-06-01'' AS DATE), true, ''x'', CAST(6.0 AS DOUBLE), ''Alpha'')'
    || ') AS v(unit_id, grp, qty, day, flag, tags, ratio, label) ' || coalesce(p_where, '')
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
