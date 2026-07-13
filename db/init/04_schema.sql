CREATE TABLE api.citizens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  unit_id text NOT NULL,
  status text NOT NULL
);

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

CREATE POLICY unit_scoped ON api.citizens
  USING (unit_id = ANY(string_to_array(current_setting('app.user_units', true), ',')));

CREATE POLICY unit_scoped ON api.service_records
  USING (unit_id = ANY(string_to_array(current_setting('app.user_units', true), ',')));

GRANT SELECT ON api.citizens TO web_anon;
GRANT SELECT ON api.service_records TO web_anon;
