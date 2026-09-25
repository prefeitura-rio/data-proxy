"""Local DuckLake catalog paths."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .settings import settings


@dataclass(frozen=True, slots=True)
class CatalogPaths:
    """Local and S3 replica locations for one schema catalog."""

    local: Path
    replica: str

    @classmethod
    def for_schema(cls, schema: str) -> CatalogPaths:
        """Build stable local and S3 replica paths for one schema."""
        local = settings.DUCKLAKE_CATALOG_LOCAL_PATH / schema / "catalog.sqlite"
        prefix = settings.DUCKLAKE_CATALOG_PATH.strip("/")
        replica = f"s3://{settings.S3_BUCKET}/{prefix}/{quote(schema, safe='')}/catalog.sqlite"
        return cls(local=local, replica=replica)
