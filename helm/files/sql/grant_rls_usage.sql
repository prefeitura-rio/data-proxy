{#
{
  "kind": "template",
  "description": "Render the grant rls usage database operation.",
  "inputs": {
    "anonymous_role": "Anonymous database role.",
    "user_role": "Application database role."
  }
}
#}
{% from "macros.sql" import grant_schema_usage %}
{{ grant_schema_usage('rls', anonymous_role) }}
{{ grant_schema_usage('rls', user_role) }}
