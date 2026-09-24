"""Local DuckLake catalog paths and Litestream configuration."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .settings import settings


@dataclass(frozen=True, slots=True)
class CatalogPaths:
    """Local and Litestream locations for one schema catalog."""

    local: Path
    replica: str

    @classmethod
    def for_schema(cls, schema: str) -> CatalogPaths:
        """Build stable local and S3 replica paths for one schema."""
        local = settings.DUCKLAKE_CATALOG_LOCAL_PATH / schema / "catalog.sqlite"
        prefix = settings.DUCKLAKE_CATALOG_PATH.strip("/")
        replica = f"s3://{settings.S3_BUCKET}/{prefix}/{quote(schema, safe='')}/catalog.sqlite"
        return cls(local=local, replica=replica)


def litestream_config(schemas: list[str]) -> str:
    """Render Litestream configuration for the configured schema catalogs."""
    if not schemas:
        return "dbs: []\n"

    endpoint_scheme = "https" if settings.S3_USE_SSL else "http"
    endpoint = f"{endpoint_scheme}://{settings.S3_ENDPOINT}"
    lines = ["dbs:"]
    for schema in schemas:
        paths = CatalogPaths.for_schema(schema)
        replica_path = paths.replica.removeprefix(f"s3://{settings.S3_BUCKET}/")
        lines.extend(
            [
                f"  - path: {paths.local}",
                "    replicas:",
                "      - type: s3",
                f"        bucket: {settings.S3_BUCKET}",
                f"        path: {replica_path}",
                f"        endpoint: {endpoint}",
                "        access-key-id: ${S3_ACCESS_KEY}",
                "        secret-access-key: ${S3_SECRET_KEY}",
            ]
        )
    return "\n".join(lines) + "\n"
