import sqlite3
import stat
from pathlib import Path

import duckdb

from data_proxy.ducklake import DuckLakePaths


def test_catalog_format_matches_pg_duckdb_support(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.sqlite"
    connection = duckdb.connect()
    connection.execute("LOAD ducklake")
    connection.execute(
        "ATTACH 'ducklake:sqlite:"
        + str(catalog)
        + "' AS dl "
        + "(DATA_PATH '"
        + str(tmp_path / "data")
        + "', DATA_INLINING_ROW_LIMIT 0)"
    )

    version = (
        sqlite3.connect(catalog)
        .execute("SELECT value FROM ducklake_metadata WHERE key = 'version'")
        .fetchone()[0]
    )

    assert version == "1.0"


def test_catalog_directory_allows_postgres_to_read(tmp_path: Path) -> None:
    paths = DuckLakePaths(
        catalog=tmp_path / "schema" / "catalog.sqlite",
        data="s3://bucket/ducklake/schema",
    )

    paths.prepare_catalog_directory()

    assert stat.S_IMODE(paths.catalog.parent.stat().st_mode) == 0o755


def test_catalog_directory_permissions_are_fixed_on_existing_path(
    tmp_path: Path,
) -> None:
    catalog_dir = tmp_path / "schema"
    catalog_dir.mkdir(mode=0o700)
    catalog_dir.chmod(0o700)
    paths = DuckLakePaths(
        catalog=catalog_dir / "catalog.sqlite",
        data="s3://bucket/ducklake/schema",
    )

    paths.prepare_catalog_directory()

    assert stat.S_IMODE(catalog_dir.stat().st_mode) == 0o755
