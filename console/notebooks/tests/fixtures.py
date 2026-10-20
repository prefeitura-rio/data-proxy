"""Write the files of the browser test: 3000 rows and a delete file that removes 300 of them."""

import sys
from pathlib import Path

import duckdb

folder = Path(sys.argv[1])
folder.mkdir(parents=True, exist_ok=True)

connection = duckdb.connect()
connection.execute(
    f"COPY (SELECT i AS id, 'name_' || i AS name FROM range(3000) t(i)) TO '{folder / 'people.parquet'}' (FORMAT parquet)"
)
connection.execute(
    f"COPY (SELECT 'people.parquet' AS file_path, (i * 10)::BIGINT AS pos FROM range(300) t(i)) "
    f"TO '{folder / 'people-delete.parquet'}' (FORMAT parquet)"
)
