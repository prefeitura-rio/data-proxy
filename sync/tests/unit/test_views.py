import re
from collections.abc import Callable
from typing import Final
from unittest.mock import AsyncMock, MagicMock

import pytest
from psycopg.sql import Identifier
from pydantic import ValidationError

import sync.views.mappings as utils
import sync.views.reconcile as views_module
from sync.models import (
    FullTable,
    PartitionedTable,
    RlsRule,
    SchemaConfig,
    SyncConfig,
    UnitMapping,
)
from sync.settings import settings
from sync.templates import render_template
from sync.types import TemplateValue
from sync.views import stages
from tests.fixtures.types import NoFallbackSource, RecordedConnection
from tests.helpers.core import exactly


def render_macro(name: str, *arguments: object) -> str:
    """Render one macro of the serving macros with the arguments of its call."""
    from sync.constants import SQL_DIR
    from sync.templates import jinja_environment

    module = (
        jinja_environment(SQL_DIR).get_template("postgres/macros.sql").make_module()
    )
    macro: Callable[..., object] = getattr(module, name)
    return str(macro(*arguments))


class TestSourceMappings:
    """Source adapter and table function mapping behavior."""

    def test_marks_nested_columns_as_json(self) -> None:
        """The table function returns nested columns as text, and the boundary view converts them to jsonb."""
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
        "duckdb_type",
        [
            pytest.param("JSON", id="json"),
            pytest.param("STRUCT(id BIGINT)", id="struct"),
            pytest.param("BIGINT[]", id="list"),
            pytest.param("VARCHAR[]", id="list-of-text"),
            pytest.param("MAP(VARCHAR, BIGINT)", id="map"),
            pytest.param("INTEGER[3]", id="array"),
            pytest.param("UNION(a INTEGER, b VARCHAR)", id="union"),
        ],
    )
    def test_marks_every_json_and_nested_type_as_json(self, duckdb_type: str) -> None:
        """Every nested type reaches the boundary view as JSON text, so the view can convert it."""
        assert utils.is_nested_or_json(duckdb_type) is True

    @pytest.mark.parametrize(
        "duckdb_type",
        [
            pytest.param("JSON", id="json"),
            pytest.param("STRUCT(id BIGINT)", id="struct"),
            pytest.param("BIGINT[]", id="list"),
            pytest.param("MAP(VARCHAR, BIGINT)", id="map"),
        ],
    )
    def test_converts_every_nested_column_to_jsonb_in_the_boundary_view(
        self, duckdb_type: str
    ) -> None:
        """A client and a JSON path in PostgREST see jsonb, as on a native table."""
        mapping = utils.boundary_view_mapping("app", "t", "t_fn", [("c", duckdb_type)])

        assert mapping["columns"] == ['"c"::jsonb AS "c"']

    @pytest.mark.parametrize(
        ("duckdb_type", "expected"),
        [
            pytest.param("BIGINT", "bigint", id="bigint"),
            pytest.param("INTEGER", "bigint", id="integer"),
            pytest.param("DATE", "date", id="date"),
            pytest.param("BOOLEAN", "boolean", id="boolean"),
            pytest.param("VARCHAR", "text", id="text"),
            pytest.param("DECIMAL(18,2)", "numeric", id="decimal"),
            pytest.param("DECIMAL(38,0)", "numeric", id="wide-decimal"),
            pytest.param("DOUBLE", "double precision", id="double"),
            pytest.param("FLOAT", "double precision", id="float"),
            pytest.param("REAL", "double precision", id="real"),
            pytest.param("TIMESTAMP", "timestamp", id="timestamp"),
            pytest.param("TIMESTAMP WITH TIME ZONE", "timestamptz", id="timestamptz"),
            pytest.param("TIMESTAMPTZ", "timestamptz", id="timestamptz-short-name"),
            pytest.param(
                "TIMESTAMP_NS", "text", id="nanoseconds-would-lose-three-digits"
            ),
            pytest.param("TIMESTAMP_MS", "timestamp", id="milliseconds"),
            pytest.param("TIMESTAMP_S", "timestamp", id="seconds"),
            pytest.param("INTERVAL", "text", id="interval"),
        ],
    )
    def test_keeps_the_type_of_a_scalar_column(
        self, duckdb_type: str, expected: str
    ) -> None:
        """A decimal and a float keep a numeric type, so PostgreSQL compares and sums them as numbers."""
        assert utils.return_type_for(duckdb_type) == expected

    def test_asks_duckdb_for_valid_json_of_every_nested_column(self) -> None:
        """DuckDB prints a STRUCT as {'a': 1} and a LIST as [t1, x], which PostgreSQL rejects as json. to_json prints valid JSON."""
        columns = utils.function_columns(
            [("doc", "STRUCT(a BIGINT)"), ("tags", "VARCHAR[]"), ("n", "BIGINT")],
            raw_json=True,
        )

        sql = render_macro("duckdb_query_select", columns, "duckdb_view")

        assert 'CAST(to_json("doc") AS VARCHAR) AS "doc"' in sql
        assert 'CAST(to_json("tags") AS VARCHAR) AS "tags"' in sql
        assert 'to_json("n")' not in sql

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
            pytest.param("people", "people_dl_chg", id="short-name"),
            pytest.param("t" * 56, "t" * 56 + "_dl_chg", id="exactly-63-bytes-is-kept"),
            pytest.param(
                "t" * 60, ("t" * 60 + "_dl_chg")[:63], id="longer-name-is-cut"
            ),
        ],
    )
    def test_cuts_the_change_feed_name_where_postgresql_cuts_it(
        self, table_name: str, expected: str
    ) -> None:
        """PostgreSQL stores an identifier of at most 63 bytes, so the reconcile must look for that name."""
        assert utils.changes_function_name(table_name) == expected
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
        ("table", "fallback_names", "rls_columns"),
        [
            pytest.param(
                FullTable(name="p.d.people", resolved_source="bigquery"),
                [],
                [],
                id="local",
            ),
            pytest.param(
                PartitionedTable(
                    name="p.d.people", resolved_source="bigquery", fallback=True
                ),
                ["bigquery"],
                [],
                id="bq",
            ),
            pytest.param(
                FullTable(
                    name="p.d.people",
                    resolved_source="bigquery",
                    rls=[
                        RlsRule(
                            conditions=[UnitMapping(column="unit_id", unit_type="unit")]
                        )
                    ],
                ),
                [],
                ["unit_id"],
                id="rls",
            ),
        ],
    )
    def test_passes_the_fallback_the_row_rules_and_the_claim_to_the_table_function(
        self,
        table: FullTable | PartitionedTable,
        fallback_names: list[str],
        rls_columns: list[str],
    ) -> None:
        """Name the fallback sources, list the columns of the row rules, and name the claim setting."""
        mapping = utils.table_function_mapping(
            "app", table, [("unit_id", "VARCHAR")], "sub"
        )

        fallbacks_value = mapping["fallbacks"]
        assert isinstance(fallbacks_value, list)
        assert [
            f["name"] for f in fallbacks_value if isinstance(f, dict)
        ] == fallback_names
        rls_value = mapping["rls_mappings"]
        assert isinstance(rls_value, list)
        assert [m["column"] for m in rls_value if isinstance(m, dict)] == rls_columns
        assert "has_rls" not in mapping
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
            pytest.param("people", "people_agg", id="short-name"),
            pytest.param("a" * 59, f"{'a' * 59}_agg", id="longest-name-that-fits"),
            pytest.param("a" * 60, None, id="one-byte-too-long"),
            pytest.param("é" * 30, None, id="bytes-not-characters"),
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
            pytest.param("a" * 60, False, id="name-too-long"),
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
            assert mapping["max_rows"] == str(settings.AGGREGATE_MAX_ROWS)
            assert mapping["function"].as_string(None) == f'"{table_name}_agg"'  # type: ignore[union-attr]
            assert mapping["dl_function"].as_string(None) == f'"{table_name}_dl_fn"'  # type: ignore[union-attr]

    @pytest.mark.parametrize(
        ("table", "template", "mapping_function", "as_list"),
        [
            pytest.param(
                FullTable(
                    name="p.d.people",
                    resolved_source="bigquery",
                    rls=[
                        RlsRule(
                            conditions=[UnitMapping(column="unit_id", unit_type="unit")]
                        )
                    ],
                ),
                "postgres/views/create_function",
                utils.table_function_mapping,
                True,
                id="table-without-fallback",
            ),
            pytest.param(
                PartitionedTable(
                    name="p.d.people",
                    resolved_source="bigquery",
                    fallback=True,
                    rls=[
                        RlsRule(
                            conditions=[UnitMapping(column="unit_id", unit_type="unit")]
                        )
                    ],
                ),
                "postgres/views/create_function",
                utils.table_function_mapping,
                False,
                id="table-with-fallback",
            ),
            pytest.param(
                FullTable(
                    name="p.d.people",
                    resolved_source="bigquery",
                    rls=[
                        RlsRule(
                            conditions=[UnitMapping(column="unit_id", unit_type="unit")]
                        )
                    ],
                ),
                "postgres/views/create_aggregate_function",
                utils.aggregate_function_mapping,
                True,
                id="aggregate",
            ),
            pytest.param(
                FullTable(
                    name="p.d.people",
                    resolved_source="bigquery",
                    rls=[
                        RlsRule(
                            conditions=[UnitMapping(column="unit_id", unit_type="unit")]
                        )
                    ],
                ),
                "postgres/create_table_changes_function",
                utils.table_changes_function_mapping,
                True,
                id="change-feed",
            ),
        ],
    )
    def test_passes_the_grants_to_duckdb_as_a_list_when_only_duckdb_reads_them(
        self,
        table: FullTable | PartitionedTable,
        template: str,
        mapping_function: Callable[..., dict[str, TemplateValue] | None],
        as_list: bool,
    ) -> None:
        """A list of 2,000 literals in an IN costs DuckDB about 150 ms, and the same list in a subquery about 40 ms. A fallback source cannot read the list, so its table keeps the literals."""
        columns = [("unit_id", "VARCHAR"), ("qty", "BIGINT")]
        mapping = mapping_function("app", table, columns, "sub")
        assert mapping is not None

        sql = render_template(template, mapping)

        has_list = (
            re.search(r"CAST\(.+ AS VARCHAR\) IN \(SELECT unnest\(\[%s\]\)\)", sql)
            is not None
        )
        assert has_list is as_list
        assert (re.search(r"%I IN \(%s\)", sql) is not None) is not as_list

    @pytest.mark.parametrize(
        ("table_name", "rendered"),
        [
            pytest.param("people", True, id="short-name"),
            pytest.param("a" * 60, False, id="name-too-long"),
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
            pytest.param("a" * 60, [], id="name-too-long"),
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
            pytest.param("people", '"people_agg"', id="short-name"),
            pytest.param("a" * 60, "", id="name-too-long"),
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

        with pytest.raises(
            RuntimeError, match=exactly("DuckDB returned no columns for table .people")
        ):
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
        ("app", "people_dl_chg"),
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


class TestColumnTypesFromDuckdb:
    """The description of a source comes back as the name and the type of each column."""

    @pytest.fixture
    def source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace the registry with a source that needs no extension."""

        def configure(*_: object) -> NoFallbackSource:
            return NoFallbackSource()

        monkeypatch.setattr(utils.sources, "configure", configure)

    @pytest.mark.usefixtures("source")
    async def test_keeps_the_name_and_the_type_of_each_row_of_a_describe(self) -> None:
        """DuckDB describes a column with six fields, and the function reads the first two."""
        connection = RecordedConnection(
            rows=[
                ("id", "BIGINT", "YES", None, None, None),
                ("name", "VARCHAR", "YES", None, None, None),
            ]
        )

        columns = await utils.column_types_from_duckdb(
            connection,  # type: ignore[arg-type]
            FullTable(name="p.d.people"),
        )

        assert columns == [("id", "BIGINT"), ("name", "VARCHAR")]

    @pytest.mark.usefixtures("source")
    async def test_refuses_a_describe_with_fewer_fields(self) -> None:
        """A row of two fields is not what DuckDB describes, so the run stops with the field count."""
        connection = RecordedConnection(rows=[("id", "BIGINT")])

        with pytest.raises(ValidationError, match="missing"):
            await utils.column_types_from_duckdb(
                connection,  # type: ignore[arg-type]
                FullTable(name="p.d.people"),
            )
