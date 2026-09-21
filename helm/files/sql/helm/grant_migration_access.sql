{#
{
  "kind": "template",
  "description": "Render the grant migration access database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "user_role": "Application database role."
  }
}
#}
{% from "macros.sql" import grant_schema_usage, grant_all_table_select %}
{{ grant_schema_usage(schema, user_role) }}
{{ grant_all_table_select(schema, user_role) }}
