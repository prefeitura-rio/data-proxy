{#
{
  "kind": "template",
  "description": "Grant access to the internal pre-request schema and function.",
  "inputs": {
    "app_schema": "PostgreSQL schema that holds the pre-request function.",
    "anonymous_role": "Anonymous database role.",
    "user_role": "Application database role."
  }
}
#}
{% from "macros.sql" import grant_schema_usage %}
{{ grant_schema_usage(app_schema, anonymous_role) }}
{{ grant_schema_usage(app_schema, user_role) }}
GRANT EXECUTE ON FUNCTION {{ app_schema }}.pre_request() TO {{ anonymous_role }};
GRANT EXECUTE ON FUNCTION {{ app_schema }}.pre_request() TO {{ user_role }};

ALTER ROLE {{ user_role }} SET statement_timeout = '15s';
ALTER ROLE {{ anonymous_role }} SET statement_timeout = '15s';
