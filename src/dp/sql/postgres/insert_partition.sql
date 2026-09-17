{#
{
  "kind": "template",
  "description": "Render the insert partition database operation.",
  "inputs": {
    "temp": "Temporary table used during the operation.",
    "columns": "Structured SQL-safe column metadata.",
    "path": "SQL-safe Parquet or object-storage path literal.",
    "predicate": "SQL predicate applied to the selected rows.",
    "schema": "PostgreSQL schema that owns the target objects.",
    "table": "PostgreSQL table being read or changed."
  }
}
#}
CREATE TEMP TABLE {{ temp }} AS
SELECT {{ columns | join(',\n    ') }}
FROM read_parquet({{ path }}) AS r
WHERE {{ predicate }};
INSERT INTO {{ schema }}.{{ table }} SELECT * FROM {{ temp }} ON CONFLICT DO NOTHING;
DROP TABLE {{ temp }}
