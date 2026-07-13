-- api.citizens/api.service_records are plain Postgres heap tables, NOT
-- pg_duckdb `USING duckdb` columnar tables -- this is required, not a
-- shortcut. Confirmed in Phase 3 (docs/phase-1-validation.md, "RLS vs
-- DuckDB execution"): DuckDB execution completely bypasses the Postgres
-- executor, including Row-Level Security. RLS can only ever apply to plain
-- heap tables. pg_duckdb/DuckDB execution (read_parquet, etc.) is used
-- exclusively in scripts/sync.py's offline batch step, never in the
-- request-serving path these tables are part of.
CREATE TABLE api.citizens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  unit_id text NOT NULL,
  status text NOT NULL
);

-- Every RLS policy below filters by unit_id via `= ANY(array)`. This index is
-- what keeps that an index scan instead of a sequential scan even when a
-- caller is authorized for 50+ units at once.
CREATE INDEX citizens_unit_id_idx ON api.citizens (unit_id);

CREATE TABLE api.service_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  citizen_id uuid NOT NULL REFERENCES api.citizens (id),
  unit_id text NOT NULL,
  protocol_type text NOT NULL,
  updated_at timestamptz NOT NULL
);

CREATE INDEX service_records_unit_id_idx ON api.service_records (unit_id);
CREATE INDEX service_records_citizen_id_idx ON api.service_records (citizen_id);

ALTER TABLE api.citizens ENABLE ROW LEVEL SECURITY;
ALTER TABLE api.service_records ENABLE ROW LEVEL SECURITY;

-- app.user_units is populated per-request by api.pre_request() (see
-- 03_pre_request.sql) from the X-User-Units header. Empty/missing header ->
-- empty string -> string_to_array gives {} -> ANY({}) is always false ->
-- fails closed (zero rows), which is the correct default when identity
-- can't be established. Verified in Phase 1 (docs/phase-1-validation.md).
CREATE POLICY unit_scoped ON api.citizens
  USING (unit_id = ANY(string_to_array(current_setting('app.user_units', true), ',')));

CREATE POLICY unit_scoped ON api.service_records
  USING (unit_id = ANY(string_to_array(current_setting('app.user_units', true), ',')));

GRANT SELECT ON api.citizens TO web_anon;
GRANT SELECT ON api.service_records TO web_anon;
