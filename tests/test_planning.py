"""Tests for planning result types."""

from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from duckdb import DuckDBPyConnection, connect
from google.cloud.bigquery import Client

from dp.models import (
    AllSelection,
    FullTable,
    PartitionedTable,
    PartitionedTablePlan,
    PartitionManifest,
    PhysicalPartition,
    RangeSelection,
    SchemaConfig,
    SyncConfig,
    SyncWork,
    TableConfig,
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
from tests.helpers import planning_partition

"""Coverage tests for planning branches."""


class TestPlanningPlanPartitioned:
    """Tests for PlanPartitioned behavior."""

    @pytest.mark.asyncio
    async def test_plan_partitioned_table_builds_changed_plan(
        self,
        bigquery: Client,
    ) -> None:
        """Verify plan partitioned table builds changed plan."""
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
        """Verify plan partitioned tables groups partitioned tables."""
        config = SyncConfig(
            schemas={"d": SchemaConfig(tables=[PartitionedTable(name="p.d.t")])}
        )
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


"""Coverage for planning orchestration branches."""


class TestPlanningBuildSync:
    """Tests for BuildSync behavior."""

    @pytest.mark.asyncio
    async def test_build_sync_work_no_changes(
        self,
    ) -> None:
        """Verify build sync work no changes."""
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
        """Verify build sync work groups full table by schema."""
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[FullTable(name="p.app.t")])}
        )
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
        """Verify build sync work groups partitioned plan."""
        config = SyncConfig(
            schemas={"app": SchemaConfig(tables=[PartitionedTable(name="p.app.t")])}
        )
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

    def test_sync_work_has_named_parts(
        self,
    ) -> None:
        """Verify sync work has named parts."""
        work = SyncWork(plans=[], tasks=[])
        assert work.plans == []
        assert work.tasks == []

    @pytest.mark.parametrize(
        ("stored", "signature", "rebuild", "changed", "removed"),
        [
            (None, "s", True, {"1"}, set[str]()),
            (
                PartitionManifest(
                    table_signature="s",
                    partitions={"1": planning_partition("1", "new")},
                ),
                "s",
                False,
                set[str](),
                set[str](),
            ),
            (
                PartitionManifest(
                    table_signature="old", partitions={"1": planning_partition("1")}
                ),
                "s",
                True,
                {"1"},
                set[str](),
            ),
            (
                PartitionManifest(
                    table_signature="s", partitions={"2": planning_partition("2")}
                ),
                "s",
                False,
                {"1"},
                {"2"},
            ),
        ],
    )
    def test_partition_changes_table_cases(
        self,
        stored: PartitionManifest | None,
        signature: str,
        rebuild: bool,
        changed: set[str],
        removed: set[str],
    ) -> None:
        """Verify partition changes table cases."""
        result = partition_changes(
            {"1": planning_partition("1", "new")}, stored, signature
        )
        assert (result.full_rebuild, result.changed, result.removed) == (
            rebuild,
            changed,
            removed,
        )

    def test_expand_config_and_json_columns(self, duckdb: DuckDBPyConnection) -> None:
        """Verify expand config and json columns."""
        config: list[TableConfig] = [FullTable(name="p.d.t")]
        with patch("dp.planning.discover_json_columns", return_value=[]):
            tasks = expand_config(config, "bucket", "run", duckdb)
        assert tasks[0].json_columns == []
        assert table_signature(config[0], "modified").startswith("modified:")

    def test_partition_tasks_order_numeric_before_remainder(
        self,
    ) -> None:
        """Verify partition tasks order numeric before remainder."""
        table = PartitionedTable(name="p.d.t")
        current = {
            "10": planning_partition("10"),
            "2": planning_partition("2"),
            "__NULL__": planning_partition("__NULL__"),
        }
        batch = build_partition_tasks(table, current, set(current), "run", "bucket", [])
        assert list(batch.paths) == ["2", "10", "__NULL__"]

    def test_discover_json_columns(self, duckdb: DuckDBPyConnection) -> None:
        """Verify discover json columns."""
        duckdb.execute("CREATE TABLE source (a STRUCT(x INTEGER), b VARCHAR)")
        with patch("dp.planning.load_template", return_value="DESCRIBE source"):
            assert discover_json_columns(duckdb, "p.d.t") == ["a"]

    @pytest.mark.asyncio
    async def test_detect_changes_filters_unchanged_and_partitioned(
        self,
        bigquery: Client,
    ) -> None:
        """Verify detect changes filters unchanged and partitioned."""
        config = SyncConfig(
            schemas={
                "d": SchemaConfig(
                    tables=[FullTable(name="p.d.t"), PartitionedTable(name="p.d.p")]
                )
            }
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
    async def test_partitioned_without_changes_returns_none(
        self,
        bigquery: Client,
    ) -> None:
        """Verify partitioned without changes returns none."""
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
        """Verify partitioned tables skips full tables."""
        config = SyncConfig(
            schemas={"d": SchemaConfig(tables=[FullTable(name="p.d.t")])}
        )
        with patch(
            "dp.planning.bigquery_clients",
            return_value=nullcontext(MagicMock(return_value=bigquery)),
        ):
            plans, tasks = await plan_partitioned_tables(
                config, (settings.redis), "run", "bucket", connect(":memory:")
            )
        assert plans == {}
        assert tasks == []
