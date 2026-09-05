"""Tests for planning result types."""

from contextlib import nullcontext
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from duckdb import DuckDBPyConnection, connect
from google.cloud.bigquery import Client

from dp.models import (
    AllSelection,
    FullTable,
    IndexConfig,
    PartitionedTable,
    PartitionedTablePlan,
    PartitionManifest,
    PhysicalPartition,
    RangeSelection,
    SyncConfig,
    SyncWork,
    TableConfig,
    UnitMapping,
)
from dp.planning import (
    build_partition_tasks,
    build_sync_work,
    detect_changes,
    discover_json_columns,
    expand_config,
    partition_changes,
    plan_partitioned_table,
    plan_partitioned_tables,
    table_signature,
)
from dp.settings import settings
from tests.helpers import planning_partition, sync_config


@dataclass(frozen=True, slots=True)
class PartitionChangeCase:
    """One partition change scenario for parametrized testing."""

    name: str
    stored: PartitionManifest | None
    signature: str
    rebuild: bool
    changed: set[str]
    removed: set[str]


pytestmark = pytest.mark.usefixtures("test_settings")


class TestPlanningPlanPartitioned:
    """Tests for PlanPartitioned behavior."""

    @pytest.mark.asyncio
    async def test_plan_partitioned_table_builds_changed_plan(
        self,
        bigquery: Client,
    ) -> None:
        """
        GIVEN: a partitioned table with changed partitions and no stored manifest.
        WHEN: plan_partitioned_table is called.
        THEN: it returns a changed plan and tasks.
        """
        table = PartitionedTable(name="p.d.t")
        current = {
            "1": PhysicalPartition(
                partition_id="1",
                signature="new",
                selection=RangeSelection(
                    partition_id="1", column="id", lower=1, upper=2
                ),
            )
        }
        with (
            patch("dp.planning.physical_partitions", return_value=("sig", current)),
            patch(
                "dp.planning.read_partition_manifest",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("dp.planning.discover_json_columns", return_value=[]),
        ):
            plan, tasks = await plan_partitioned_table(
                table,
                bigquery,
                (settings.redis),
                "r",
                "b",
                connect(":memory:"),
            )

        assert plan is not None
        assert tasks

    @pytest.mark.asyncio
    async def test_plan_partitioned_tables_groups_partitioned_tables(
        self,
        bigquery: Client,
    ) -> None:
        """
        GIVEN: a config with one partitioned table.
        WHEN: plan_partitioned_tables is called.
        THEN: it groups the table plan by table name.
        """
        config = sync_config([PartitionedTable(name="p.d.t")], schema_name="d")
        table_plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=True,
            current_partitions={},
            changed_paths={},
            removed_partitions={},
        )
        with (
            patch(
                "dp.planning.bigquery_clients",
                return_value=nullcontext(MagicMock(return_value=bigquery)),
            ),
            patch(
                "dp.planning.plan_partitioned_table",
                new_callable=AsyncMock,
                return_value=(table_plan, []),
            ),
        ):
            plans, tasks = await plan_partitioned_tables(
                config, (settings.redis), "r", "b", connect(":memory:")
            )
        assert plans == {"p.d.t": table_plan}
        assert tasks == []


class TestPlanningBuildSync:
    """Tests for BuildSync behavior."""

    @pytest.mark.asyncio
    async def test_build_sync_work_returns_empty_when_no_changes(
        self,
    ) -> None:
        """
        GIVEN: an empty sync config with no changes.
        WHEN: build_sync_work is called.
        THEN: it returns empty plans and tasks.
        """
        config = SyncConfig(schemas={})
        with (
            patch(
                "dp.planning.detect_changes", new_callable=AsyncMock, return_value={}
            ),
            patch(
                "dp.planning.plan_partitioned_tables",
                new_callable=AsyncMock,
                return_value=({}, []),
            ),
        ):
            result = await build_sync_work(
                config, (settings.redis), "r1", "b", connect(":memory:")
            )
        assert result == SyncWork(plans=[], tasks=[])

    @pytest.mark.asyncio
    async def test_build_sync_work_groups_full_table_by_schema(
        self,
    ) -> None:
        """
        GIVEN: a config with a changed full table.
        WHEN: build_sync_work is called.
        THEN: it groups the full table task by schema.
        """
        config = sync_config([FullTable(name="p.app.t")])
        task = config.tables[0].to_task("r1", "b", AllSelection())
        with (
            patch(
                "dp.planning.detect_changes",
                new_callable=AsyncMock,
                return_value={"p.app.t": "sig"},
            ),
            patch("dp.planning.expand_config", return_value=[task]),
            patch(
                "dp.planning.plan_partitioned_tables",
                new_callable=AsyncMock,
                return_value=({}, []),
            ),
        ):
            result = await build_sync_work(
                config, (settings.redis), "r1", "b", connect(":memory:")
            )
        assert len(result.plans) == 1
        assert result.plans[0].schema_name == "app"
        assert result.tasks == [task]


class TestPlanning:
    """Tests for planning module behavior."""

    @pytest.mark.asyncio
    async def test_build_sync_work_groups_partitioned_plan(
        self,
    ) -> None:
        """
        GIVEN: a config with a partitioned table plan.
        WHEN: build_sync_work is called.
        THEN: it groups the partitioned plan by schema.
        """
        config = sync_config([PartitionedTable(name="p.app.t")])
        table_plan = PartitionedTablePlan(
            table_signature="s",
            full_rebuild=True,
            current_partitions={},
            changed_paths={},
            removed_partitions={},
        )
        with (
            patch(
                "dp.planning.detect_changes", new_callable=AsyncMock, return_value={}
            ),
            patch(
                "dp.planning.plan_partitioned_tables",
                new_callable=AsyncMock,
                return_value=({"p.app.t": table_plan}, []),
            ),
        ):
            work = await build_sync_work(
                config, (settings.redis), "r", "b", connect(":memory:")
            )
        assert work.plans[0].partitioned_tables["p.app.t"] == table_plan

    def test_sync_work_exposes_plans_and_tasks_lists(
        self,
    ) -> None:
        """
        GIVEN: an empty SyncWork.
        WHEN: its parts are accessed.
        THEN: plans and tasks are both empty.
        """
        work = SyncWork(plans=[], tasks=[])
        assert work.plans == []
        assert work.tasks == []

    @pytest.mark.parametrize(
        "case",
        [
            PartitionChangeCase("new", None, "s", True, {"1"}, set[str]()),
            PartitionChangeCase(
                "unchanged",
                PartitionManifest(
                    table_signature="s",
                    partitions={"1": planning_partition("1", "new")},
                ),
                "s",
                False,
                set[str](),
                set[str](),
            ),
            PartitionChangeCase(
                "rebuild",
                PartitionManifest(
                    table_signature="old", partitions={"1": planning_partition("1")}
                ),
                "s",
                True,
                {"1"},
                set[str](),
            ),
            PartitionChangeCase(
                "removed",
                PartitionManifest(
                    table_signature="s", partitions={"2": planning_partition("2")}
                ),
                "s",
                False,
                {"1"},
                {"2"},
            ),
        ],
        ids=lambda case: case.name,
    )
    def test_partition_changes_detects_new_unchanged_rebuild_and_removed(
        self, case: PartitionChangeCase
    ) -> None:
        """
        GIVEN: current partitions and a stored manifest with various change scenarios.
        WHEN: partition_changes is called.
        THEN: it returns the correct rebuild, changed, and removed sets.
        """
        result = partition_changes(
            {"1": planning_partition("1", "new")},
            case.stored,
            case.signature,
        )
        assert (result.full_rebuild, result.changed, result.removed) == (
            case.rebuild,
            case.changed,
            case.removed,
        )

    def test_expand_config_discovers_json_columns_for_full_tables(
        self, duckdb: DuckDBPyConnection
    ) -> None:
        """
        GIVEN: a full table config with no JSON columns.
        WHEN: expand_config is called.
        THEN: tasks have empty json_columns and the table signature is derived from the modified column.
        """
        config: list[TableConfig] = [FullTable(name="p.d.t")]
        with patch("dp.planning.discover_json_columns", return_value=[]):
            tasks = expand_config(config, "bucket", "run", duckdb)
        assert tasks[0].json_columns == []
        assert table_signature(config[0], None, "modified").startswith("modified:")

    def test_partition_tasks_order_numeric_before_remainder(
        self,
    ) -> None:
        """
        GIVEN: partitions with numeric and __NULL__ ids.
        WHEN: build_partition_tasks is called.
        THEN: numeric partitions are ordered before the remainder.
        """
        table = PartitionedTable(name="p.d.t")
        current = {
            "10": planning_partition("10"),
            "2": planning_partition("2"),
            "__NULL__": planning_partition("__NULL__"),
        }
        batch = build_partition_tasks(table, current, set(current), "run", "bucket", [])
        assert list(batch.paths) == ["2", "10", "__NULL__"]

    def test_discover_json_columns_returns_only_struct_columns(
        self, duckdb: DuckDBPyConnection
    ) -> None:
        """
        GIVEN: a source table with a STRUCT column and a VARCHAR column.
        WHEN: discover_json_columns is called.
        THEN: it returns only the STRUCT column name.
        """
        duckdb.execute("CREATE TABLE source (a STRUCT(x INTEGER), b VARCHAR)")
        with patch("dp.planning.load_template", return_value="DESCRIBE source"):
            assert discover_json_columns(duckdb, "p.d.t") == ["a"]

    @pytest.mark.asyncio
    async def test_detect_changes_filters_unchanged_and_partitioned(
        self,
        bigquery: Client,
    ) -> None:
        """
        GIVEN: a config with an unchanged full table and a partitioned table.
        WHEN: detect_changes is called.
        THEN: only the changed full table is returned.
        """
        config = sync_config(
            [FullTable(name="p.d.t"), PartitionedTable(name="p.d.p")],
            schema_name="d",
        )
        with (
            patch(
                "dp.planning.bigquery_clients",
                return_value=nullcontext(MagicMock(return_value=bigquery)),
            ),
            patch("dp.planning.table_modified", return_value="m"),
            patch(
                "dp.planning.read_table_signature",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await detect_changes(config, (settings.redis))
        assert set(result) == {"p.d.t"}

    @pytest.mark.asyncio
    async def test_partitioned_table_returns_none_when_unchanged(
        self,
        bigquery: Client,
    ) -> None:
        """
        GIVEN: a partitioned table with no changes since the stored manifest.
        WHEN: plan_partitioned_table is called.
        THEN: it returns no plan and no tasks.
        """
        table = PartitionedTable(name="p.d.t")
        with (
            patch(
                "dp.planning.physical_partitions",
                return_value=("s", {"1": planning_partition("1", "new")}),
            ),
            patch(
                "dp.planning.read_partition_manifest",
                new_callable=AsyncMock,
                return_value=PartitionManifest(
                    table_signature="s",
                    partitions={"1": planning_partition("1", "new")},
                ),
            ),
        ):
            plan, tasks = await plan_partitioned_table(
                table,
                bigquery,
                (settings.redis),
                "run",
                "bucket",
                connect(":memory:"),
            )
        assert plan is None
        assert tasks == []

    @pytest.mark.asyncio
    async def test_partitioned_tables_skips_full_tables(self, bigquery: Client) -> None:
        """
        GIVEN: a config with only full tables.
        WHEN: plan_partitioned_tables is called.
        THEN: it returns no plans and no tasks.
        """
        config = sync_config([FullTable(name="p.d.t")], schema_name="d")
        with patch(
            "dp.planning.bigquery_clients",
            return_value=nullcontext(MagicMock(return_value=bigquery)),
        ):
            plans, tasks = await plan_partitioned_tables(
                config, (settings.redis), "run", "bucket", connect(":memory:")
            )
        assert plans == {}
        assert tasks == []


@dataclass(frozen=True, slots=True)
class SignatureCase:
    """One table_signature change detection scenario."""

    name: str
    table_a: TableConfig
    claim_a: str | None
    table_b: TableConfig
    claim_b: str | None
    should_differ: bool


class TestTableSignature:
    """Tests for table_signature change detection scoping."""

    @pytest.mark.parametrize(
        "case",
        [
            SignatureCase(
                "rls change",
                FullTable(
                    name="p.d.t", rls=[UnitMapping(column="id", unit_type="unit")]
                ),
                None,
                FullTable(
                    name="p.d.t", rls=[UnitMapping(column="id", unit_type="cras")]
                ),
                None,
                True,
            ),
            SignatureCase(
                "indexes change",
                FullTable(
                    name="p.d.t", indexes=[IndexConfig(name="idx", columns=["col"])]
                ),
                None,
                FullTable(
                    name="p.d.t",
                    indexes=[IndexConfig(name="idx", columns=["col"], method="gin")],
                ),
                None,
                True,
            ),
            SignatureCase(
                "claim change",
                FullTable(name="p.d.t"),
                "old_claim",
                FullTable(name="p.d.t"),
                "new_claim",
                True,
            ),
            SignatureCase(
                "resolved_schema ignored",
                FullTable(name="p.d.t", resolved_schema="schema_x"),
                None,
                FullTable(name="p.d.t", resolved_schema="schema_y"),
                None,
                False,
            ),
        ],
        ids=lambda case: case.name,
    )
    def test_signature_scoping(self, case: SignatureCase) -> None:
        """
        GIVEN: two table configs that differ in one field.
        WHEN: table_signature is computed for both.
        THEN: signatures differ iff the field affects extraction or publication.
        """
        sig_a = table_signature(case.table_a, case.claim_a, "m")
        sig_b = table_signature(case.table_b, case.claim_b, "m")
        assert (sig_a != sig_b) == case.should_differ
