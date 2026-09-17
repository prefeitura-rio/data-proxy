{#
{
  "kind": "template",
  "description": "List tables owned by a PostgreSQL schema for stale-table cleanup.",
  "inputs": {
    "schema": "SQL-safe PostgreSQL schema literal."
  }
}
#}
SELECT tablename
FROM pg_tables
WHERE schemaname = {{ schema }}
