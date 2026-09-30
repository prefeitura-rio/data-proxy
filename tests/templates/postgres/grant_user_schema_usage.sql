{#
{
  "kind": "template",
  "description": "Grant schema usage to the user role.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "user_role": "SQL-safe user role identifier."
  }
}
#}
GRANT USAGE ON SCHEMA {{ schema }} TO {{ user_role }}
