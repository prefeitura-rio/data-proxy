-- PostgREST v12 does not expose individual headers as `request.header.<name>`
-- GUCs (that convention is from older PostgREST versions) -- only the full
-- header set as a single JSON blob under `request.headers`. Confirmed via
-- direct testing against postgrest/postgrest:v12.2.8 (see Phase 1 validation
-- notes in .sisyphus/plans/poc-pedro-architecture.md).
CREATE OR REPLACE FUNCTION api.pre_request() RETURNS void AS $$
BEGIN
  PERFORM set_config(
    'app.user_units',
    coalesce(
      (current_setting('request.headers', true)::json ->> 'x-user-units'),
      ''
    ),
    true
  );
END;
$$ LANGUAGE plpgsql;
