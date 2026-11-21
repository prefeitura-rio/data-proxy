{#
{
  "kind": "template",
  "description": "Create one application schema when it is absent, and let the request role use it.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "user_role": "Database role that every token switches to."
  }
}
#}
CREATE SCHEMA IF NOT EXISTS {{ schema }};
GRANT USAGE ON SCHEMA {{ schema }} TO {{ user_role }}
