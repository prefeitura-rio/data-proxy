{#
{
  "kind": "template",
  "description": "Create a function that maps JWT claims into PostgreSQL session variables, loads the rbac rules for the role and the schemas of the token, and gives a request to the console profile a longer statement timeout.",
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

    PERFORM rbac.load();

    -- The console reads views over the DBOS tables, so its requests may run longer than data requests.
    -- The role of the token has the short timeout, and every token has the same role.
    IF coalesce(
        nullif(current_setting('request.headers', true), '')::json ->> 'accept-profile',
        nullif(current_setting('request.headers', true), '')::json ->> 'content-profile'
    ) = 'console' THEN
        PERFORM set_config('statement_timeout', '30s', true);
    END IF;
END;
$$ LANGUAGE plpgsql
