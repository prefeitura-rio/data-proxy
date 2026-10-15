{#
{
  "kind": "template",
  "description": "Let a console permission level run the pre-request function, which copies the claims of the token, such as its schemas, into session settings, and bound its queries in time. PostgREST applies the settings of the request role only, so each level needs its own call.",
  "inputs": {
    "app_schema": "PostgreSQL schema that holds the pre-request function.",
    "console_role": "Console database role: one of the four permission levels."
  }
}
#}
{% from "macros.sql" import grant_schema_usage %}
{{ grant_schema_usage(app_schema, console_role) }}
GRANT EXECUTE ON FUNCTION {{ app_schema }}.pre_request() TO {{ console_role }};

ALTER ROLE {{ console_role }} SET statement_timeout = '30s';
