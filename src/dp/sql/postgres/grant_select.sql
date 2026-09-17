{#
{
  "kind": "template",
  "description": "Render the grant select database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "table": "PostgreSQL table being read or changed.",
    "user_role": "Application database role."
  }
}
#}
GRANT SELECT ON {{ schema }}.{{ table }} TO {{ user_role }}
