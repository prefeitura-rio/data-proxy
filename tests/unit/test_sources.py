"""Behavior tests for source registry and mappings."""

from typing import Final
from unittest.mock import AsyncMock

import pytest

import data_proxy.sources.stages as stages
import data_proxy.sources.utils as utils
import data_proxy.sources.views as views_module
from data_proxy.models import FullTable, SchemaConfig, SyncConfig, UnitMapping
from data_proxy.settings import settings
from data_proxy.sources.sources import Source, Sources, sources
from data_proxy.templates import to_sql


class TestSourcesRegistry:
    """Registry lookup and validation behavior."""

    def test_get_raises_for_an_unknown_source(self) -> None:
        """Reject a source name that is not registered."""
        with pytest.raises(ValueError, match="unknown source: clickhouse"):
            sources.get("clickhouse")

    @pytest.mark.parametrize("as_list", [False, True], ids=["one", "list"])
    def test_registers_one_source_or_a_list(self, as_list: bool) -> None:
        """Register sources given alone or in a list."""
        clickhouse = Source(
            name="clickhouse",
            kind="remote",
            load="LOAD clickhouse",
            scan=lambda table: f"ch_scan('{table}')",
            suffix="ch_fn",
            arg="covered",
        )

        registry = Sources().register([clickhouse] if as_list else clickhouse)

        assert registry.get("clickhouse") is clickhouse

    def test_config_rejects_an_unknown_fallback(self) -> None:
        """Reject a table that names an unregistered fallback source."""
        with pytest.raises(ValueError, match="unknown source: nope"):
            SyncConfig(
                schemas={
                    "app": SchemaConfig(
                        tables=[FullTable(name="p.d.people", fallbacks=["nope"])]
                    )
                }
            )


class TestSourceMappings:
    """Source adapter and table function mapping behavior."""

    def test_marks_nested_columns_as_json(self) -> None:
        """Map nested DuckDB columns to JSONB-compatible output."""
        columns = utils.function_columns(
            [("payload", "STRUCT(id BIGINT)"), ("count", "BIGINT")],
            raw_json=False,
        )

        assert columns[0]["is_json"] is True
        assert columns[0]["pg_type"] == "text"
        assert columns[0]["return_type"] == "text"
        assert columns[1]["is_json"] is False
        assert columns[1]["pg_type"] == "bigint"

    def test_desires_one_view_per_table(self) -> None:
        """Serve every table through one view, with or without fallback."""
        config = SyncConfig(
            schemas={
                "app": SchemaConfig(
                    tables=[
                        FullTable(name="p.d.people", fallbacks=["bigquery"]),
                        FullTable(name="p.d.private"),
                    ]
                )
            }
        )

        assert stages.desired_views(config) == {
            ("app", "people"),
            ("app", "private"),
        }

    @pytest.mark.parametrize(
        ("table", "fallback_names", "has_rls"),
        [
            pytest.param(FullTable(name="p.d.people"), [], "false", id="local"),
            pytest.param(
                FullTable(name="p.d.people", fallbacks=["bigquery"]),
                ["bigquery"],
                "false",
                id="bq",
            ),
            pytest.param(
                FullTable(
                    name="p.d.people",
                    rls=[UnitMapping(column="unit_id", unit_type="unit")],
                ),
                [],
                "true",
                id="rls",
            ),
        ],
    )
    def test_maps_the_table_function_to_its_helpers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        table: FullTable,
        fallback_names: list[str],
        has_rls: str,
    ) -> None:
        """Name the helpers and pass the fallback and RLS settings."""
        config = SyncConfig(schemas={"app": SchemaConfig(claim="sub", tables=[table])})
        monkeypatch.setitem(settings.__dict__, "sync_config", config)

        mapping = utils.table_function_mapping("app", table, [("unit_id", "VARCHAR")])

        assert to_sql(mapping["function"]) == '"people_fn"'
        assert to_sql(mapping["dl_function"]) == '"people_dl_fn"'
        assert to_sql(mapping["source_table"]) == "'p.d.people'"
        fallbacks_value = mapping["fallbacks"]
        assert isinstance(fallbacks_value, list)
        assert [
            f["name"] for f in fallbacks_value if isinstance(f, dict)
        ] == fallback_names
        assert mapping["has_rls"] == has_rls
        assert mapping["claim_setting"] == "app.claim_sub"

    def test_source_function_mapping_uses_adapter_values(self) -> None:
        """Build a fallback helper mapping from the adapter registry."""
        table = FullTable(name="p.d.people", fallbacks=["bigquery"])

        mapping = utils.source_function_mapping(
            "app", table, [("id", "BIGINT")], sources.get("bigquery")
        )

        assert to_sql(mapping["function"]) == '"people_bq_fn"'
        assert mapping["load"] == "LOAD bigquery"
        assert mapping["source"] == "bigquery_scan(''p.d.people'')"

    def test_ducklake_function_mapping_scans_the_quoted_table(self) -> None:
        """Build the DuckLake helper mapping over the quoted catalog table."""
        table = FullTable(name="p.d.people")

        mapping = utils.ducklake_function_mapping("app", table, [("id", "BIGINT")])

        assert to_sql(mapping["function"]) == '"people_dl_fn"'
        assert mapping["source"] == 'dl."people"'


class TestCreateTableViews:
    """Table view creation edge cases."""

    @pytest.mark.asyncio
    async def test_rejects_tables_without_discovered_columns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reject a table when DuckDB returns no source columns."""
        monkeypatch.setattr(
            stages,
            "column_types_from_duckdb",
            AsyncMock(return_value=[]),
        )

        with pytest.raises(RuntimeError, match="no columns"):
            await stages.create_table_views(
                AsyncMock(),
                "app",
                FullTable(name="p.d.people"),
            )

    @pytest.mark.asyncio
    async def test_does_not_drop_protected_views(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keep protected PostgreSQL views during reconciliation cleanup."""
        executor = AsyncMock()
        monkeypatch.setattr(stages, "Executor", executor)

        await stages.drop_removed_views(AsyncMock(), {("app", "access_policy")})

        executor.assert_not_called()


CONFIGURED_VIEWS: Final = frozenset({("app", "people")})
CONFIGURED_FUNCTIONS: Final = frozenset(
    {("app", "ducklake_latest_snapshot"), ("app", "ducklake_changes_people")}
)


class TestReconciliationChange:
    """The changed flag decides whether PostgREST must restart."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("views", "functions", "changed"),
        [
            pytest.param(CONFIGURED_VIEWS, CONFIGURED_FUNCTIONS, False, id="unchanged"),
            pytest.param(
                frozenset[tuple[str, str]](),
                CONFIGURED_FUNCTIONS,
                True,
                id="view-added",
            ),
            pytest.param(
                CONFIGURED_VIEWS | {("app", "old")},
                CONFIGURED_FUNCTIONS,
                True,
                id="view-removed",
            ),
            pytest.param(
                CONFIGURED_VIEWS,
                frozenset[tuple[str, str]](),
                True,
                id="functions-added",
            ),
        ],
    )
    async def test_reports_whether_the_view_set_changed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        views: frozenset[tuple[str, str]],
        functions: frozenset[tuple[str, str]],
        changed: bool,
    ) -> None:
        """Compare the configured objects with the objects in the database."""
        monkeypatch.setattr(
            views_module, "existing_views", AsyncMock(return_value=views)
        )
        monkeypatch.setattr(
            views_module, "existing_functions", AsyncMock(return_value=functions)
        )
        context = views_module.ReconciliationContext(
            pg_conn=AsyncMock(),
            config=SyncConfig(
                schemas={"app": SchemaConfig(tables=[FullTable(name="p.d.people")])}
            ),
        )

        await context.detect()

        assert context.changed is changed
