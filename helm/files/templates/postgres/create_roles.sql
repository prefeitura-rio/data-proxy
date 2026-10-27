{#
{
  "kind": "template",
  "description": "Create the roles that the requests use: the anonymous role, one role for each role of rbac.toml, and the serving login. Every role of rbac.toml is a member of the baseline role, so the grants of the baseline role serve them all. The serving login is a member of every request role and doesn't inherit their rights. Each request role gets the statement timeout of the data API, because PostgREST applies the settings of the role that it switches to, and a membership doesn't pass settings on. The signer login of the console gets a password login without inheritance on each run. Run it as the owner role, which is a superuser. The template creates a missing role, sets the login, the lack of inheritance and the password of the serving login on each run, because pg_duckdb creates the role that duckdb.postgres_role names, without a login, when its extension is created. A membership that exists is granted again without an error.",
  "inputs": {
    "roles": "SQL literals, separated by commas, of the roles of rbac.toml.",
    "baseline_role": "SQL literal of the role that every role of rbac.toml is a member of.",
    "anonymous_role": "SQL literal of the role of a request without a token.",
    "serving_role": "SQL literal of the login that PostgREST connects with.",
    "serving_password": "SQL literal of the password of that login.",
    "signer_role": "SQL literal of the login that the console uses to read the catalog. It has no membership here. The reader role of each schema grants it one.",
    "signer_password": "SQL literal of the password of that login."
  }
}
#}
DO $create_roles$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[{{ roles }}, {{ anonymous_role }}] LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', role_name);
        END IF;
        EXECUTE format('ALTER ROLE %I SET statement_timeout = %L', role_name, '15s');
    END LOOP;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {{ serving_role }}) THEN
        EXECUTE format('CREATE ROLE %I', {{ serving_role }});
    END IF;
    EXECUTE format('ALTER ROLE %I LOGIN NOINHERIT PASSWORD %L', {{ serving_role }}, {{ serving_password }});

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {{ signer_role }}) THEN
        EXECUTE format('CREATE ROLE %I', {{ signer_role }});
    END IF;
    EXECUTE format('ALTER ROLE %I LOGIN NOINHERIT PASSWORD %L', {{ signer_role }}, {{ signer_password }});

    FOREACH role_name IN ARRAY ARRAY[{{ roles }}] LOOP
        IF role_name <> {{ baseline_role }} THEN
            EXECUTE format('GRANT %I TO %I', {{ baseline_role }}, role_name);
        END IF;
    END LOOP;

    FOREACH role_name IN ARRAY ARRAY[{{ roles }}, {{ anonymous_role }}] LOOP
        EXECUTE format('GRANT %I TO %I', role_name, {{ serving_role }});
    END LOOP;
END
$create_roles$
