{#
{
  "kind": "template",
  "description": "Remove access-policy rows for a PostgreSQL schema.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
TRUNCATE {{ schema }}.access_policy
