{#
{
  "kind": "template",
  "description": "List tables that still own BigQuery fallback objects in a PostgreSQL schema.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema literal."
  }
}
#}
SELECT DISTINCT left(c.relname, -3)
FROM pg_class AS c
INNER JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE
    n.nspname = {{ schema }}
    AND c.relkind = 'v'
    AND right(c.relname, 3) = '_bq'
UNION
SELECT DISTINCT left(p.proname, -6)
FROM pg_proc AS p
INNER JOIN pg_namespace AS n ON n.oid = p.pronamespace
WHERE
    n.nspname = {{ schema }}
    AND right(p.proname, 6) = '_bq_fn'
