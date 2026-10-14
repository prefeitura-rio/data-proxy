import pytest

from sync.ducklake import DuckLakePaths
from sync.settings import settings


@pytest.mark.parametrize("schema", ["app", "bigquery_a"])
def test_each_schema_gets_its_own_metadata_schema_in_the_catalog_database(
    schema: str,
) -> None:
    """Point every schema at the catalog database, with the schema name as the metadata schema."""
    paths = DuckLakePaths.for_schema(schema)

    assert paths == DuckLakePaths(
        catalog=f"ducklake:postgres:{settings.DUCKLAKE_CATALOG_URL}",
        data=f"s3://{settings.S3_BUCKET}/{settings.DUCKLAKE_CATALOG_PATH}/{schema}",
        metadata_schema=schema,
    )
