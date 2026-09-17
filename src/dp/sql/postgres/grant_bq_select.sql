{#
{
  "kind": "template",
  "description": "Render the grant bq select database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "view": "PostgreSQL view being created or granted.",
    "user_role": "Application database role."
  }
}
#}
GRANT SELECT ON {{ schema }}.{{ view }} TO {{ user_role }}
