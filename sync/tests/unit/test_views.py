from typing import Final
from unittest.mock import AsyncMock, MagicMock

import pytest
from psycopg.sql import Identifier

import sync.views.mappings as utils
import sync.views.reconcile as views_module
from sync.models import (
    FullTable,
    PartitionedTable,
    SchemaConfig,
    SyncConfig,
    UnitMapping,
)
from sync.views import stages


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

    @pytest.mark.parametrize(
        ("duckdb_type", "sortable"),
        [
            pytest.param("BIGINT", True, id="bigint"),
            pytest.param("INTEGER", True, id="integer"),
            pytest.param("DATE", True, id="date"),
            pytest.param("BOOLEAN", True, id="boolean"),
            pytest.param("VARCHAR", False, id="text-sorts-by-collation"),
            pytest.param("DOUBLE", False, id="double-reaches-postgresql-as-text"),
            pytest.param("TIMESTAMP", False, id="timestamp-reaches-postgresql-as-text"),
            pytest.param("STRUCT(id BIGINT)", False, id="json"),
            pytest.param("INTERVAL", False, id="interval"),
        ],
    )
    def test_marks_the_columns_that_both_engines_sort_the_same_way(
        self, duckdb_type: str, sortable: bool
    ) -> None:
        """Mark a column sortable only when PostgreSQL receives a bigint, a date, or a boolean."""
        columns = utils.function_columns([("c", duckdb_type)], raw_json=True)

        assert columns[0]["sortable"] is sortable

    @pytest.mark.parametrize(
        ("table_name", "expected"),
        [
            pytest.param("people", "dl_changes_people", id="short-name"),
            pytest.param(
                "t" * 52, "dl_changes_" + "t" * 52, id="exactly-63-bytes-is-kept"
            ),
            pytest.param(
                "t" * 60, ("dl_changes_" + "t" * 60)[:63], id="longer-name-is-cut"
            ),
        ],
    )
    def test_cuts_the_change_feed_name_where_postgresql_cuts_it(
        self, table_name: str, expected: str
    ) -> None:
        """PostgreSQL stores an identifier of at most 63 bytes, so the reconcile must look for that name."""
        table = FullTable(name=f"p.app.{table_name}")

        assert utils.changes_function_name(table) == expected
        assert len(expected.encode()) <= 63

    def test_desires_one_view_per_table(self) -> None:
        """Serve every table through one view, with or without fallback."""
        config = SyncConfig(
            schemas={
                "app": SchemaConfig(
                    tables=[
                        PartitionedTable(name="p.d.people", fallback=True),
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
            pytest.param(
                FullTable(name="p.d.people", resolved_source="bigquery"),
                [],
                "false",
                id="local",
            ),
            pytest.param(
                PartitionedTable(
                    name="p.d.people", resolved_source="bigquery", fallback=True
                ),
                ["bigquery"],
                "false",
                id="bq",
            ),
            pytest.param(
                FullTable(
                    name="p.d.people",
                    resolved_source="bigquery",
                    rls=[UnitMapping(column="unit_id", unit_type="unit")],
                ),
                [],
                "true",
                id="rls",
            ),
        ],
    )
    def test_passes_the_fallback_the_rls_flag_and_the_claim_to_the_table_function(
        self,
        table: FullTable | PartitionedTable,
        fallback_names: list[str],
        has_rls: str,
    ) -> None:
        """Name the fallback sources, say whether the table has row rules, and name the claim setting."""
        mapping = utils.table_function_mapping(
            "app", table, [("unit_id", "VARCHAR")], "sub"
        )

        fallbacks_value = mapping["fallbacks"]
        assert isinstance(fallbacks_value, list)
        assert [
            f["name"] for f in fallbacks_value if isinstance(f, dict)
        ] == fallback_names
        assert mapping["has_rls"] == has_rls
        assert mapping["claim_setting"] == "app.claim_sub"

    def test_keeps_the_same_signature_for_equal_serving_definitions(self) -> None:
        """Do not recreate a view when generated serving SQL is unchanged."""
        table = FullTable(name="p.d.people", resolved_source="bigquery")

        first = utils.serving_definition_signature(
            "app", table, [("id", "BIGINT")], "sub"
        )
        second = utils.serving_definition_signature(
            "app", table, [("id", "BIGINT")], "sub"
        )

        assert first == second


class TestAggregateFunction:
    """The aggregate function of a table has a name that PostgreSQL keeps whole."""

    @pytest.mark.parametrize(
        ("duckdb_type", "numeric"),
        [
            pytest.param("BIGINT", True, id="bigint"),
            pytest.param("INTEGER", True, id="integer"),
            pytest.param("HUGEINT", True, id="hugeint"),
            pytest.param("DOUBLE", True, id="double"),
            pytest.param("FLOAT", True, id="float"),
            pytest.param("DECIMAL(18,3)", True, id="decimal-with-a-scale"),
            pytest.param("decimal(10,2)", True, id="decimal-in-lower-case"),
            pytest.param("VARCHAR", False, id="text"),
            pytest.param("DATE", False, id="date"),
            pytest.param("BOOLEAN", False, id="boolean"),
            pytest.param("TIMESTAMP", False, id="timestamp"),
            pytest.param("INTERVAL", False, id="interval-is-not-a-number"),
            pytest.param("STRUCT(id BIGINT)", False, id="json"),
        ],
    )
    def test_marks_the_types_that_duckdb_sums_and_averages(
        self, duckdb_type: str, numeric: bool
    ) -> None:
        """Mark a column numeric by its DuckDB type, because PostgreSQL receives decimals as text."""
        columns = utils.function_columns([("c", duckdb_type)], raw_json=True)

        assert columns[0]["numeric"] is numeric

    @pytest.mark.parametrize(
        ("duckdb_type", "groupable"),
        [
            pytest.param("VARCHAR", True, id="text"),
            pytest.param("BIGINT", True, id="bigint"),
            pytest.param("INTEGER", True, id="integer"),
            pytest.param("DATE", True, id="date"),
            pytest.param("BOOLEAN", True, id="boolean"),
            pytest.param("varchar", True, id="text-in-lower-case"),
            pytest.param("DOUBLE", False, id="double-reaches-postgresql-as-text"),
            pytest.param(
                "DECIMAL(18,3)", False, id="decimal-reaches-postgresql-as-text"
            ),
            pytest.param("SMALLINT", False, id="smallint-reaches-postgresql-as-text"),
            pytest.param("TIMESTAMP", False, id="timestamp-reaches-postgresql-as-text"),
            pytest.param("STRUCT(id BIGINT)", False, id="json"),
        ],
    )
    def test_marks_the_types_that_a_group_by_gives_as_the_table_does(
        self, duckdb_type: str, groupable: bool
    ) -> None:
        """Mark a column groupable only when DuckDB and the table function give the same value."""
        columns = utils.function_columns([("c", duckdb_type)], raw_json=True)

        assert columns[0]["groupable"] is groupable

    @pytest.mark.parametrize(
        ("table_name", "expected"),
        [
            pytest.param("people", "people_aggregate", id="short-name"),
            pytest.param(
                "a" * 53, f"{'a' * 53}_aggregate", id="longest-name-that-fits"
            ),
            pytest.param("a" * 54, None, id="one-byte-too-long"),
            pytest.param("é" * 27, None, id="bytes-not-characters"),
        ],
    )
    def test_names_the_function_only_when_the_name_fits(
        self, table_name: str, expected: str | None
    ) -> None:
        """Give no name when PostgreSQL would cut it, because two cut names can match."""
        assert utils.aggregate_function_name(table_name) == expected

    @pytest.mark.parametrize(
        ("table_name", "has_mapping"),
        [
            pytest.param("people", True, id="short-name"),
            pytest.param("a" * 54, False, id="name-too-long"),
        ],
    )
    def test_maps_the_function_of_a_table_with_a_short_name(
        self, table_name: str, has_mapping: bool
    ) -> None:
        """Reuse the mapping of the table function with the name of the aggregate function."""
        table = FullTable(name=f"p.d.{table_name}", resolved_source="bigquery")

        mapping = utils.aggregate_function_mapping(
            "app", table, [("qty", "BIGINT")], "sub"
        )

        assert (mapping is not None) is has_mapping
        if mapping is not None:
            assert mapping["function"].as_string(None) == f'"{table_name}_aggregate"'  # type: ignore[union-attr]
            assert mapping["dl_function"].as_string(None) == f'"{table_name}_dl_fn"'  # type: ignore[union-attr]

    @pytest.mark.parametrize(
        ("table_name", "rendered"),
        [
            pytest.param("people", True, id="short-name"),
            pytest.param("a" * 54, False, id="name-too-long"),
        ],
    )
    def test_puts_the_function_in_the_serving_signature(
        self, monkeypatch: pytest.MonkeyPatch, table_name: str, rendered: bool
    ) -> None:
        """Change the signature when the function changes, so the next reconcile creates it."""
        seen: list[str] = []
        render = utils.render_template

        def record(name: str, mapping: dict[str, object], **kwargs: object) -> str:
            seen.append(name)
            return render(name, mapping, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(utils, "render_template", record)

        utils.serving_definition_signature(
            "app",
            FullTable(name=f"p.d.{table_name}", resolved_source="bigquery"),
            [("qty", "BIGINT")],
            "sub",
        )

        assert ("postgres/views/create_aggregate_function" in seen) is rendered

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("table_name", "templates"),
        [
            pytest.param(
                "people", ["postgres/views/create_aggregate_function"], id="short-name"
            ),
            pytest.param("a" * 54, [], id="name-too-long"),
        ],
    )
    async def test_creates_the_function_only_for_a_short_name(
        self, table_name: str, templates: list[str]
    ) -> None:
        """Skip the function for a table whose name PostgreSQL would cut."""
        executor = AsyncMock()

        await stages.create_aggregate_function(
            executor,
            "app",
            FullTable(name=f"p.d.{table_name}", resolved_source="bigquery"),
            [("qty", "BIGINT")],
            "sub",
        )

        assert [call.args[0] for call in executor.execute.await_args_list] == templates

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("view_name", "expected"),
        [
            pytest.param("people", '"people_aggregate"', id="short-name"),
            pytest.param("a" * 54, "", id="name-too-long"),
        ],
    )
    async def test_drops_the_function_with_the_view(
        self, monkeypatch: pytest.MonkeyPatch, view_name: str, expected: str
    ) -> None:
        """Drop the aggregate function of a view that leaves the config, and never a cut name."""
        executor = AsyncMock()
        executor_class = MagicMock()
        executor_class.__getitem__.return_value.return_value = executor
        monkeypatch.setattr(stages, "Executor", executor_class)

        await stages.drop_view_objects(AsyncMock(), "app", view_name)

        mapping: dict[str, Identifier | str] = executor.execute.await_args.kwargs[
            "mapping"
        ]
        aggregate = mapping["aggregate_function"]
        assert (
            aggregate.as_string(None)
            if isinstance(aggregate, Identifier)
            else aggregate
        ) == expected


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
                [],
                None,
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
    {
        ("app", "dl_latest_snapshot"),
        ("app", "dl_table_files"),
        ("app", "dl_changes_people"),
    }
)


class TestReconciliationChange:
    """The changed flag decides whether PostgREST must reload."""

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
        monkeypatch.setattr(
            views_module, "existing_view_signatures", AsyncMock(return_value={})
        )
        context = views_module.ReconciliationContext(
            pg_conn=AsyncMock(),
            config=SyncConfig(
                schemas={"app": SchemaConfig(tables=[FullTable(name="p.d.people")])}
            ),
            schema_names=["app"],
        )

        await context.detect()

        assert context.postgrest_reload_required is changed

    @pytest.mark.asyncio
    async def test_skips_unplanned_schemas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reconcile only schemas that have a sync plan."""
        reconcile = AsyncMock(return_value=False)
        monkeypatch.setattr(views_module, "reconcile_schema", reconcile)
        context = views_module.ReconciliationContext(
            pg_conn=AsyncMock(),
            config=SyncConfig(
                schemas={
                    "one": SchemaConfig(tables=[FullTable(name="p.d.one")]),
                    "two": SchemaConfig(tables=[FullTable(name="p.d.two")]),
                }
            ),
            schema_names=["one"],
        )

        await context.reconcile()

        assert [call.args[1] for call in reconcile.await_args_list] == ["one"]
        assert context.postgrest_reload_required is False

    @pytest.mark.asyncio
    async def test_marks_recreated_objects_as_a_schema_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reload PostgREST after table functions and views are recreated."""
        monkeypatch.setattr(
            views_module, "reconcile_schema", AsyncMock(return_value=True)
        )
        context = views_module.ReconciliationContext(
            pg_conn=AsyncMock(),
            config=SyncConfig(
                schemas={"app": SchemaConfig(tables=[FullTable(name="p.d.people")])}
            ),
            schema_names=["app"],
        )

        await context.reconcile()

        assert context.postgrest_reload_required is True
