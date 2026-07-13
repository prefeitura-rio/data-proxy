CREATE OR REPLACE FUNCTION api.pre_request() RETURNS void AS $$
BEGIN
  PERFORM set_config(
    'app.user_units',
    coalesce(current_setting('request.header.x-user-units', true), ''),
    true
  );
END;
$$ LANGUAGE plpgsql;
