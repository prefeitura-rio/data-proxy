{#
{
  "kind": "template",
  "description": "Render the grant schema usage backup database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects."
  }
}
#}
{% from "macros.sql" import grant_schema_usage %}
{{ grant_schema_usage(schema, 'backup') }}
