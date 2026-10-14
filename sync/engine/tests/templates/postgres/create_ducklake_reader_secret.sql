{#
{
  "kind": "template",
  "description": "Create the pg_duckdb secrets that point the DuckLake reader at the catalog database.",
  "inputs": {
    "host": "Catalog database host.",
    "port": "Catalog database port.",
    "database": "Catalog database name.",
    "user": "Catalog reader role.",
    "password": "Catalog reader password."
  }
}
#}
-- noqa: disable=LT05
SELECT duckdb.raw_query(
    'CREATE OR REPLACE PERSISTENT SECRET ducklake_reader_pg ('
    || 'TYPE postgres, HOST ''{{ host }}'', PORT {{ port }}, DATABASE ''{{ database }}'', '
    || 'USER ''{{ user }}'', PASSWORD ''{{ password }}'')'
);
SELECT duckdb.raw_query(
    'CREATE OR REPLACE PERSISTENT SECRET ducklake_reader ('
    || 'TYPE ducklake, METADATA_PATH '''', '
    || 'METADATA_PARAMETERS MAP {''TYPE'': ''postgres'', ''SECRET'': ''ducklake_reader_pg''})'
)
