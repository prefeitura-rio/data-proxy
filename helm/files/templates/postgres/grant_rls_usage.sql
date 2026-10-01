{#
{
  "kind": "template",
  "description": "Grant USAGE on the rls schema to the anonymous and application roles.",
  "inputs": {
    "anonymous_role": "Anonymous database role.",
    "user_role": "Application database role."
  }
}
#}
{% from "macros.sql" import grant_schema_usage %}
{{ grant_schema_usage('rls', anonymous_role) }}
{{ grant_schema_usage('rls', user_role) }}

ALTER ROLE {{ user_role }} SET statement_timeout = '15s';
ALTER ROLE {{ anonymous_role }} SET statement_timeout = '15s';
