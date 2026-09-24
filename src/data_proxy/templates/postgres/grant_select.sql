{#
{
  "kind": "template",
  "description": "Grant SELECT on one schema object (table or view) to the application role.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target object.",
    "name": "SQL-safe identifier for the table or view.",
    "user_role": "Application database role."
  }
}
#}
GRANT SELECT ON {{ schema }}.{{ name }} TO {{ user_role }}
