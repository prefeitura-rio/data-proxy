{#
{
  "kind": "template",
  "description": "Create one function that returns a constant.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "function": "SQL-safe function identifier."
  }
}
#}
CREATE FUNCTION {{ schema }}.{{ function }}() RETURNS integer
LANGUAGE sql AS 'SELECT 1'
