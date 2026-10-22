{#
{
  "kind": "template",
  "description": "Load one ingestion source extension in the pg_duckdb session.",
  "inputs": {
    "load": "SQL-safe DuckDB extension load statement."
  }
}
#}
SELECT duckdb.raw_query({{ load }});
