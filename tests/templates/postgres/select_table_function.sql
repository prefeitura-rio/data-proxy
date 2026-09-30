{#
{
  "kind": "template",
  "description": "Select the rows of the stub table function.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema identifier."
  }
}
#}
SELECT
    source,
    arg1,
    arg2
FROM {{ schema }}.t_fn()
ORDER BY source
