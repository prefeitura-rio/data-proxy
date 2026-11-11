"""Create DuckLake catalogs and tables, and count their rows."""

from pathlib import Path

import duckdb
from psycopg.sql import Identifier, Literal

from sync.duckdb import DuckDB
from sync.executor import Executor
from sync.settings import settings
from sync.templates import render_template
from sync.types import (
    DatabaseRow,
    DuckDBParams,
)
from tests.constants import (
    PARQUET,
    S3_SECRET_INPUTS,
    TEST_SQL_DIR,
)


async def attach_ducklake(duckdb: DuckDB, tmp_path: Path) -> None:
    """Attach one file-based DuckLake catalog for the test connection."""
    await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
        "duckdb/attach",
        mapping={
            "catalog": Literal(f"ducklake:sqlite:{tmp_path / 'catalog.sqlite'}"),
            "data_path": Literal(str(tmp_path / "data")),
            "encrypted": False,
        },
    )


async def create_ducklake_table(
    duckdb: DuckDB, table: str, parquet: str = PARQUET
) -> None:
    """Create one DuckLake table from a Parquet schema."""
    await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).execute(
        "duckdb/create_table",
        mapping={"table": Identifier(table)},
        params=[parquet],
    )


async def ducklake_row_count(duckdb: DuckDB, table: str) -> int:
    """Return the row count of one DuckLake table."""
    rows = await duckdb.query(
        render_template(
            "duckdb/row_count", {"table": Identifier(table)}, root=TEST_SQL_DIR
        )
    )
    row_count = rows[0][0]
    assert isinstance(row_count, int)
    return row_count


async def ducklake_columns(duckdb: DuckDB, table: str) -> set[str]:
    """Return the column names of one DuckLake table."""
    rows = await Executor[DuckDBParams, list[DatabaseRow]](conn=duckdb).query(
        "duckdb/describe_table",
        mapping={"table": Identifier(table)},
        expect=tuple[str, str],
    )
    return {row[0] for row in rows}


def publish_people_catalog(metadata_schema: str, data_path: str) -> None:
    """Write the people catalog with the DuckDB of the sync worker, not the database one."""
    script = render_template(
        "duckdb/publish_people_snapshots",
        {
            "catalog_url": settings.DUCKLAKE_CATALOG_URL.get_secret_value(),
            "metadata_schema": metadata_schema,
            "data_path": data_path,
            **S3_SECRET_INPUTS,
            "first": "s3://test-bucket/app/people/people_partition_10.parquet",
            "second": "s3://test-bucket/app/people/people_partition_20.parquet",
        },
        root=TEST_SQL_DIR,
    )
    connection = duckdb.connect()
    try:
        for statement in filter(str.strip, script.split(";\n")):
            connection.execute(statement)
    finally:
        connection.close()


async def count_rows(duckdb: DuckDB, path: Path, where: str = "TRUE") -> int:
    """Count the rows of one Parquet file that match a condition."""
    rows = await duckdb.query(
        f"SELECT count(*) FROM read_parquet('{path}') WHERE {where}"
    )
    count = rows[0][0]
    assert isinstance(count, int)
    return count


async def snapshot_count(duckdb: DuckDB) -> int:
    """Return how many snapshots the attached catalog holds."""
    rows = await duckdb.query("SELECT count(*) FROM ducklake_snapshots('dl')")
    count = rows[0][0]
    assert isinstance(count, int)
    return count


def write_unsorted_batches(directory: Path, count: int) -> list[str]:
    """Write one Parquet file for each batch, with rows of a contiguous key range in random order."""
    paths: list[str] = []
    connection = duckdb.connect()

    for batch in range(count):
        path = str(directory / f"batch-{batch}.parquet")
        connection.execute(
            f"""
            COPY (
                SELECT {batch} * 1000000 + (hash(i) % 1000000)::BIGINT AS cpf, i AS number
                FROM range({batch * 30000}, {(batch + 1) * 30000}) AS t (i)
                ORDER BY hash(i + 1)
            ) TO '{path}' (FORMAT parquet, ROW_GROUP_SIZE 10000)
            """
        )
        paths.append(path)

    connection.close()

    return paths


def write_tagged_rows(path: Path, lower: int, upper: int, tag: str) -> str:
    """Write one Parquet file with one row for each integer of [lower, upper), marked with the tag."""
    connection = duckdb.connect()
    connection.execute(
        f"COPY (SELECT i AS cpf, '{tag}' AS tag FROM range({lower}, {upper}) AS t (i)) "
        f"TO '{path}' (FORMAT parquet)"
    )
    connection.close()

    return str(path)


async def data_files(duckdb: DuckDB) -> int:
    """Return the number of data files of the table people."""
    rows = await duckdb.query(
        "SELECT count(*) FROM ducklake_list_files('dl', 'people')"
    )
    count = rows[0][0]
    assert isinstance(count, int)

    return count
