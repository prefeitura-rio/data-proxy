{#
{
  "kind": "template",
  "description": "Render the create table from parquet database operation.",
  "inputs": {
    "schema": "PostgreSQL schema that owns the target objects.",
    "table": "PostgreSQL table being read or changed.",
    "path": "SQL-safe Parquet or object-storage path literal."
  }
}
#}
DROP TABLE IF EXISTS {{ schema }}.{{ table }};
CREATE TABLE {{ schema }}.{{ table }}
AS SELECT * FROM read_parquet({{ path }})
