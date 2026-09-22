{#
{
  "kind": "template",
  "description": "Drop one stale table and its dependent objects through the SECURITY DEFINER helper.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier.",
    "table": "SQL-safe PostgreSQL table literal."
  }
}
#}
SELECT {{ schema }}.drop_table_if_exists({{ table }})
