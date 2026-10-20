"""Seed the test catalog and its table files function for the console tests."""

from os import environ

import duckdb
from psycopg import connect
from psycopg.sql import Identifier, Literal

from sync.ducklake import DuckLakePaths
from sync.settings import settings
from sync.templates import render_template

SCHEMA = "test"

paths = DuckLakePaths.for_schema(SCHEMA)

db = duckdb.connect()

for extension in ("httpfs", "ducklake", "postgres_scanner"):
    db.install_extension(extension)

db.execute(
    render_template(
        "duckdb/setup",
        {
            "s3_key_id": Literal(settings.S3_ACCESS_KEY),
            "s3_secret_key": Literal(settings.S3_SECRET_KEY),
            "s3_endpoint": Literal(settings.S3_ENDPOINT),
            "s3_use_ssl": "true" if settings.S3_USE_SSL else "false",
            "source_extensions": [],
        },
    )
)

db.execute(
    render_template(
        "duckdb/attach",
        {
            "catalog": Literal(paths.catalog),
            "data_path": Literal(paths.data),
            "metadata_schema": Literal(paths.metadata_schema),
            "encrypted": False,
        },
    )
)

db.execute(
    "CREATE TABLE dl.full_table AS SELECT i AS n, 'unit_' || (i % 5 + 1) AS unit_id FROM range(5000) AS t(i)"
)

db.execute("DELETE FROM dl.full_table WHERE n < 100")

db.execute(
    f"COPY (SELECT count(*) FROM dl.full_table) TO '{environ['EXPECTED_ROWS_FILE']}' (FORMAT csv, HEADER false)"
)

with connect(settings.PG_DATABASE_URL, autocommit=True) as connection:
    connection.execute(
        render_template(
            "postgres/create_table_files_function",
            {
                "schema": Identifier(SCHEMA),
                "function": Identifier("dl_table_files"),
                "metadata_schema": SCHEMA,
                "data_path": paths.data,
            },
        )
    )
