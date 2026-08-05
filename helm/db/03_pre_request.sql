-- This function reads the JWT claims and sets the session variable for row-level security.
CREATE OR REPLACE FUNCTION rls.pre_request() RETURNS void AS $$
BEGIN
    PERFORM set_config(
        'app.user_units',
        coalesce(
            (
                SELECT string_agg(value, ',')
                FROM json_array_elements_text(
                    coalesce(
                        current_setting('request.jwt.claims', true)::json -> 'unidades',
                        '[]'::json
                    )
                )
            ),
            ''
        ),
        true
    );
END;
$$ LANGUAGE plpgsql;
