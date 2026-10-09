{#
{
  "kind": "template",
  "description": "Grant access to the internal pre-request schema and function.",
  "inputs": {
    "app_schema": "PostgreSQL schema that holds the pre-request function.",
    "anonymous_role": "Anonymous database role.",
    "user_role": "Application database role.",
    "policy_writer_roles": "Per-schema policy-writer database roles."
  }
}
#}
{% from "macros.sql" import grant_schema_usage %}
{{ grant_schema_usage(app_schema, anonymous_role) }}
{{ grant_schema_usage(app_schema, user_role) }}
GRANT EXECUTE ON FUNCTION {{ app_schema }}.pre_request() TO {{ anonymous_role }};
GRANT EXECUTE ON FUNCTION {{ app_schema }}.pre_request() TO {{ user_role }};
{% for role in policy_writer_roles %}
{{ grant_schema_usage(app_schema, role) }}
GRANT EXECUTE ON FUNCTION {{ app_schema }}.pre_request() TO {{ role }};
{% endfor %}

ALTER ROLE {{ user_role }} SET statement_timeout = '15s';
ALTER ROLE {{ anonymous_role }} SET statement_timeout = '15s';
