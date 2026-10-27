from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

import sync.sources.bigquery.clients as bigquery_clients
from sync.sources.bigquery.source import BigQuerySource
from sync.sources.partitions import RangeConfig, RemainderSelection, TimeConfig
from sync.sources.registry import sources
from sync.sources.source import Sources
from sync.templates import to_sql


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


class TestPartitionConfigurationRules:
    """A partition configuration that cannot work is refused when it is built."""

    @pytest.mark.parametrize(
        ("config", "reason"),
        [
            pytest.param(
                lambda: RangeConfig(field="", start=0, end=10, interval=1),
                "field must not be empty",
                id="range-without-field",
            ),
            pytest.param(
                lambda: RangeConfig(field="cpf", start=0, end=10, interval=0),
                "interval must be positive",
                id="range-with-no-interval",
            ),
            pytest.param(
                lambda: RangeConfig(field="cpf", start=0, end=10, interval=-5),
                "interval must be positive",
                id="range-with-a-negative-interval",
            ),
            pytest.param(
                lambda: RangeConfig(field="cpf", start=10, end=10, interval=1),
                "start must precede end",
                id="range-that-is-empty",
            ),
            pytest.param(
                lambda: RangeConfig(field="cpf", start=20, end=10, interval=1),
                "start must precede end",
                id="range-that-is-reversed",
            ),
            pytest.param(
                lambda: TimeConfig(field=""),
                "field must not be empty",
                id="time-without-field",
            ),
            pytest.param(
                lambda: RemainderSelection(column="cpf", start=5, end=5),
                "start must precede end",
                id="remainder-that-is-empty",
            ),
        ],
    )
    def test_refuses_the_configuration_and_says_why(
        self, config: Callable[[], object], reason: str
    ) -> None:
        """Name the rule that the configuration breaks."""
        with pytest.raises(ValueError, match=reason):
            config()

    def test_accepts_a_valid_range_and_a_valid_time_field(self) -> None:
        """Build the two configurations when their rules hold."""
        assert RangeConfig(field="cpf", start=0, end=10, interval=2).interval == 2
        assert TimeConfig(field="day").field == "day"
