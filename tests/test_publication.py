"""Coverage for Publication defensive branches."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

from dp.models import PartitionedTablePlan, PhysicalPartition, SyncPlan
from dp.publication import partition_predicate, planned_paths


class TestPublication:
    """Tests for publication input validation."""

    def test_planned_paths_rejects_invalid_partition_plan(
        self,
    ) -> None:
        """Verify planned paths rejects invalid partition plan."""
        with pytest.raises(AssertionError):
            planned_paths(
                SyncPlan(schema_name="app"),
                "p.d.t",
                cast("PartitionedTablePlan", cast(object, "invalid")),
            )

    def test_partition_predicate_rejects_invalid_selection_branch(
        self,
    ) -> None:
        """Verify partition predicate rejects invalid selection branch."""
        with (
            patch("dp.publication.selection_fields", return_value={}),
            pytest.raises(AssertionError),
        ):
            partition_predicate(
                cast(
                    "PhysicalPartition",
                    cast(object, SimpleNamespace(selection=object())),
                )
            )
