{#
{
  "kind": "template",
  "description": "Render the grant schema usage backup database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects."
  }
}
#}
GRANT USAGE ON SCHEMA {{ schema }} TO backup;
