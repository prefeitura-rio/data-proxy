{#
{
  "kind": "template",
  "description": "Create a function that maps JWT claims into PostgreSQL session variables.",
  "inputs": {
    "schema": "PostgreSQL schema that holds the pre-request function."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ schema }};

CREATE OR REPLACE FUNCTION {{ schema }}.pre_request() RETURNS void AS $$
DECLARE
    claims json := coalesce(
        current_setting('request.jwt.claims', true)::json,
        '{}'::json
    );
    claim_name text;
    claim_value json;
BEGIN
    FOR claim_name, claim_value IN
        SELECT * FROM json_each(claims)
    LOOP
        PERFORM set_config(
            'app.claim_' || claim_name,
            CASE json_typeof(claim_value)
                WHEN 'array' THEN (
                    SELECT string_agg(value, ',')
                    FROM json_array_elements_text(claim_value)
                )
                ELSE trim(both '"' FROM claim_value::text)
            END,
            true
        );
    END LOOP;
END;
$$ LANGUAGE plpgsql
