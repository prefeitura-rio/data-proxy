{#
{
  "kind": "template",
  "description": "Let the authenticator switch to a policy-writer role, and let the role call the pre-request function.",
  "inputs": {
    "app_schema": "PostgreSQL schema that holds the pre-request function.",
    "policy_writer_role": "SQL-safe policy-writer role identifier.",
    "authenticator_role": "SQL-safe authenticator role identifier."
  }
}
#}
{% from "macros.sql" import grant_schema_usage %}
GRANT {{ policy_writer_role }} TO {{ authenticator_role }};
{{ grant_schema_usage(app_schema, policy_writer_role) }}
GRANT EXECUTE ON FUNCTION {{ app_schema }}.pre_request() TO {{ policy_writer_role }}
