"""Tests for ingestion source adapters."""

from unittest.mock import AsyncMock

import pytest

import data_proxy.sources.bigquery.clients as bigquery_clients
from data_proxy.sources.bigquery.source import BigQuerySource
from data_proxy.sources.registry import sources
from data_proxy.sources.source import PartitionedSource, Sources
from data_proxy.templates import to_sql


class TestSources:
    """Source registry behavior tests."""

    def test_rejects_unsupported_bigquery_settings(self) -> None:
        """Reject settings that BigQuery does not define."""
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            sources.configure("bigquery", {"location": "southamerica-east1"})

    def test_preserves_registered_metadata_and_isolates_clients(self) -> None:
        """Configure a fresh source without replacing registered metadata."""
        registered = BigQuerySource(name="bq-alt", load="LOAD alternate")
        registry = Sources().register([registered])

        first = registry.configure("bq-alt", None)
        second = registry.configure("bq-alt", None)

        assert isinstance(first, BigQuerySource)
        assert isinstance(second, BigQuerySource)
        assert first.name == "bq-alt"
        assert first.load == "LOAD alternate"
        assert first is not registered
        assert second is not first
        assert second.clients is not first.clients

    def test_rejects_duplicate_source_names(self) -> None:
        """Require each configured source type to have one definition."""
        registry = Sources()
        registry.register([BigQuerySource()])

        with pytest.raises(ValueError, match="already registered"):
            registry.register([BigQuerySource()])

    def test_rejects_an_unknown_source(self) -> None:
        """Report every known source for an invalid lookup."""
        registry = Sources().register([BigQuerySource()])

        with pytest.raises(
            ValueError, match=r"unknown source: missing \(known sources: bigquery\)"
        ):
            registry.configure("missing", {})


class TestBigQuerySource:
    """BigQuery adapter contract tests."""

    def test_supports_physical_partition_sync(self) -> None:
        """Declare BigQuery as a partition-capable source."""
        assert isinstance(BigQuerySource(), PartitionedSource)

    def test_renders_a_safe_scan_expression(self) -> None:
        """Render the existing DuckDB BigQuery scan expression."""
        assert to_sql(BigQuerySource().scan("project.dataset.table")) == (
            "bigquery_scan('project.dataset.table')"
        )

    @pytest.mark.asyncio
    async def test_reuses_and_closes_one_project_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reuse one client for every table in one BigQuery project."""
        client = AsyncMock()
        create = AsyncMock(return_value=client)
        monkeypatch.setattr(bigquery_clients.BigQuery, "create", create)
        source = BigQuerySource()

        assert await source.client_for("project.dataset.one") is client
        assert await source.client_for("project.dataset.two") is client
        create.assert_awaited_once_with("project")

        await source.close()
        client.close.assert_awaited_once()
        assert source.clients == {}

    @pytest.mark.parametrize(
        "reference",
        ["project.dataset", "project.dataset.table.extra", "not a reference"],
    )
    def test_rejects_an_invalid_table_reference(self, reference: str) -> None:
        """Reject references outside the BigQuery project.dataset.table format."""
        with pytest.raises(ValueError, match="Invalid BigQuery table reference"):
            BigQuerySource().validate(reference)
