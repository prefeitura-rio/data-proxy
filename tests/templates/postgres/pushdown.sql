DROP SCHEMA IF EXISTS pushdown CASCADE;
CREATE SCHEMA pushdown;

CREATE TABLE pushdown.items_data (
    id integer PRIMARY KEY,
    name text NOT NULL,
    active boolean NOT NULL,
    owner text NOT NULL
);

INSERT INTO pushdown.items_data (id, name, active, owner)
VALUES
    (1, 'Rio', true, 'alice'),
    (2, 'Tokyo', false, 'bob'),
    (3, 'Paris', true, 'bob'),
    (4, 'Rio Grande', false, 'alice');

ALTER TABLE pushdown.items_data ENABLE ROW LEVEL SECURITY;
CREATE POLICY items_owner_policy ON pushdown.items_data
    USING (
        COALESCE(
            NULLIF(CURRENT_SETTING('request.jwt.claims', true), '')::jsonb ->> 'sub',
            owner
        ) = owner
    );

CREATE FUNCTION pushdown.items()
RETURNS TABLE (id integer, name text, active boolean)
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    filter jsonb := data_proxy.request_filter();
    predicate text := data_proxy.render_ducklake_where(
        filter, ARRAY['id', 'name', 'active']::text[]
    );
    query text := 'SELECT id, name, active FROM pushdown.items_data';
BEGIN
    IF predicate IS NOT NULL THEN
        query := query || ' WHERE ' || predicate;
    END IF;
    query := query || ' ORDER BY id';
    RETURN QUERY EXECUTE query;
END;
$$;

GRANT USAGE ON SCHEMA data_proxy TO anon;
GRANT USAGE ON SCHEMA pushdown TO anon;
GRANT EXECUTE ON FUNCTION pushdown.items() TO anon;
GRANT SELECT ON pushdown.items_data TO anon;
