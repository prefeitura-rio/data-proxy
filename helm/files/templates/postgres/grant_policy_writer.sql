{#
{
  "kind": "template",
  "description": "Let the authenticator switch to a policy-writer role, and let the role call the pre-request function. CNPG owns the memberships of the authenticator and removes the others, so the role joins policy_writer, a static role that CNPG gives to the authenticator.",
  "inputs": {
    "app_schema": "PostgreSQL schema that holds the pre-request function.",
    "policy_writer_role": "SQL-safe policy-writer role identifier."
  }
}
#}
{% from "macros.sql" import grant_schema_usage %}
GRANT {{ policy_writer_role }} TO policy_writer;
{{ grant_schema_usage(app_schema, policy_writer_role) }}
GRANT EXECUTE ON FUNCTION {{ app_schema }}.pre_request() TO {{ policy_writer_role }}
