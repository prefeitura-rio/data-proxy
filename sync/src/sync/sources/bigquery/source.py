"""BigQuery ingestion source adapter."""

import json
import re
from dataclasses import dataclass, field, replace
from typing import ClassVar

from psycopg.sql import SQL, Composable, Literal
from pydantic import BaseModel, ConfigDict, JsonValue

from ...constants import BIGQUERY_TABLE_REFERENCE_PATTERN
from ..partitions import PartitionRequest, PhysicalPartition
from ..source import Fallback
from .clients import BigQuery
from .fallback import BigQueryFallback
from .partitions import (
    parse_table_reference,
    physical_partitions,
    table_modified,
)


class BigQuerySourceSettings(BaseModel):
    """Non-secret settings accepted by the BigQuery source."""

    model_config: ClassVar[ConfigDict] = ConfigDict({"extra": "forbid"})


@dataclass
class BigQuerySource:
    """Build BigQuery expressions and validate BigQuery table references."""

    name: str = "bigquery"
    fallback: Fallback | None = field(default_factory=BigQueryFallback)
    load: str = "LOAD bigquery"
    extensions: tuple[str, ...] = ("bigquery",)
    settings: BigQuerySourceSettings = field(default_factory=BigQuerySourceSettings)
    clients: dict[str, BigQuery] = field(default_factory=dict)

    def with_settings(self, settings: dict[str, JsonValue] | None) -> BigQuerySource:
        """Return a fresh source with validated non-secret settings."""
        return replace(
            self,
            settings=BigQuerySourceSettings.model_validate(settings or {}),
            clients={},
        )

    def validate(self, table: str) -> None:
        """Reject a non-BigQuery project.dataset.table reference."""
        if re.fullmatch(BIGQUERY_TABLE_REFERENCE_PATTERN, table) is None:
            raise ValueError(f"Invalid BigQuery table reference: {table}")

    def scan(self, table: str) -> Composable:
        """Return a SQL-safe DuckDB BigQuery scan expression."""
        self.validate(table)
        return SQL("bigquery_scan({})").format(Literal(table))

    async def modified(self, table: str) -> str:
        """Return the BigQuery modification value for one source table."""
        return await table_modified(await self.client_for(table), table)

    async def partitions(
        self, request: PartitionRequest
    ) -> tuple[str, dict[str, PhysicalPartition]]:
        """Return the BigQuery partition signature and physical partitions."""
        return await physical_partitions(
            await self.client_for(request.table),
            request.table,
            json.dumps(request.config, sort_keys=True),
            request.keep_latest,
        )

    async def client_for(self, table: str) -> BigQuery:
        """Return the cached client for the table project."""
        reference = parse_table_reference(table)

        client = self.clients.get(reference.project)
        if client is None:
            client = await BigQuery.create(reference.project)
            self.clients[reference.project] = client

        return client

    async def close(self) -> None:
        """Close every cached project client."""
        for client in self.clients.values():
            await client.close()

        self.clients.clear()
