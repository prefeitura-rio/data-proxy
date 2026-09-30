{#
{
  "kind": "template",
  "description": "Snapshot request and validation functions."
}
#}
-- noqa: disable=PRS,LT05

CREATE OR REPLACE FUNCTION {{ schema }}.requested_snapshot() RETURNS bigint
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_value text;
BEGIN
  SELECT header.value INTO v_value
  FROM json_each_text(
    coalesce(nullif(current_setting('request.headers', true), '')::json, '{}'::json)
  ) AS header
  WHERE lower(header.key) = 'x-ducklake-snapshot';

  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  IF v_value !~ '^[0-9]{1,18}$' THEN
    RAISE EXCEPTION 'x-ducklake-snapshot must be a whole number' USING ERRCODE = 'PT400';
  END IF;

  RETURN v_value::bigint;
END;
$$;

CREATE OR REPLACE FUNCTION {{ schema }}.assert_snapshot_exists(p_snapshot bigint) RETURNS void
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_found bigint;
BEGIN
  PERFORM duckdb.raw_query(
    'CREATE OR REPLACE VIEW ducklake_snapshot_check AS '
    || 'SELECT count(*) AS n FROM dl.snapshots() WHERE snapshot_id = ' || p_snapshot
  );

  SELECT r['n']::bigint INTO v_found FROM duckdb.query('SELECT n FROM ducklake_snapshot_check') r;

  IF v_found = 0 THEN
    RAISE EXCEPTION 'snapshot % does not exist', p_snapshot USING ERRCODE = 'PT404';
  END IF;
END;
$$;
