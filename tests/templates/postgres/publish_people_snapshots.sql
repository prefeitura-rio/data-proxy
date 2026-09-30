{#
{
  "kind": "template",
  "description": "Publish two DuckLake snapshots of people, one statement per snapshot.",
  "inputs": {
    "catalog": "Local DuckLake catalog path.",
    "data_path": "DuckLake data path.",
    "first": "Parquet path of the first snapshot.",
    "second": "Parquet path of the second snapshot."
  }
}
#}
SELECT duckdb.raw_query(
    'ATTACH ''ducklake:sqlite:{{ catalog }}'' AS lake (DATA_PATH ''{{ data_path }}'')'
);
SELECT duckdb.raw_query(
    'CREATE TABLE lake.people AS SELECT * FROM read_parquet(''{{ first }}'')'
);
SELECT duckdb.raw_query(
    'INSERT INTO lake.people SELECT * FROM read_parquet(''{{ second }}'')'
);
