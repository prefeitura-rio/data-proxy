{#
{
  "kind": "template",
  "description": "List managed view definition signatures in PostgreSQL schemas.",
  "inputs": {
    "schemas": "SQL-safe PostgreSQL text-array literal."
  }
}
#}
SELECT
  n.nspname AS schema_name,
  c.relname AS view_name,
  obj_description(c.oid, 'pg_class') AS signature
FROM pg_class AS c
INNER JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE c.relkind = 'v'
  AND n.nspname = any({{ schemas }}::text[])
