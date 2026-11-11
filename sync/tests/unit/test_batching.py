import pytest

from sync.batching import merge, runs
from sync.sources.partitions import (
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from tests.fixtures.types import RunCase
from tests.helpers.state import daily, partition, ranged, universe


class TestRuns:
    """Contiguous changed partitions form one run, so one query and one file cover many partitions."""

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                RunCase(
                    partitions=universe(ranged(0, 100), ranged(1, 100), ranged(2, 100)),
                    changed={"0", "10", "20"},
                    max_bytes=None,
                    expected=[["0", "10", "20"]],
                ),
                id="no-limit-merges-every-contiguous-partition",
            ),
            pytest.param(
                RunCase(
                    partitions=universe(ranged(0, 100), ranged(1, 100), ranged(2, 100)),
                    changed={"0", "10", "20"},
                    max_bytes=250,
                    expected=[["0", "10"], ["20"]],
                ),
                id="the-byte-target-closes-a-run",
            ),
            pytest.param(
                RunCase(
                    partitions=universe(ranged(0, 100), ranged(1, 100), ranged(2, 100)),
                    changed={"0", "10", "20"},
                    max_bytes=200,
                    expected=[["0", "10"], ["20"]],
                ),
                id="a-run-may-reach-the-target-exactly",
            ),
            pytest.param(
                RunCase(
                    partitions=universe(ranged(0, 500), ranged(1, 100)),
                    changed={"0", "10"},
                    max_bytes=250,
                    expected=[["0"], ["10"]],
                ),
                id="a-partition-over-the-target-is-its-own-run",
            ),
            pytest.param(
                RunCase(
                    partitions=universe(
                        ranged(0, 100), ranged(1, 100), ranged(2, 100), ranged(3, 100)
                    ),
                    changed={"0", "20", "30"},
                    max_bytes=None,
                    expected=[["0"], ["20", "30"]],
                ),
                id="an-unchanged-partition-splits-a-run",
            ),
            pytest.param(
                RunCase(
                    partitions=universe(ranged(0, 100), ranged(1, 100)),
                    changed=set(),
                    max_bytes=250,
                    expected=[],
                ),
                id="nothing-changed",
            ),
            pytest.param(
                RunCase(
                    partitions=universe(ranged(0, 100), ranged(5, 100)),
                    changed={"0", "50"},
                    max_bytes=None,
                    expected=[["0", "50"]],
                ),
                id="a-gap-without-a-partition-does-not-split-a-run",
            ),
            pytest.param(
                RunCase(
                    partitions=universe(ranged(2, 100), ranged(0, 100), ranged(1, 100)),
                    changed={"0", "10", "20"},
                    max_bytes=None,
                    expected=[["0", "10", "20"]],
                ),
                id="runs-follow-the-order-of-the-key-not-of-the-listing",
            ),
            pytest.param(
                RunCase(
                    partitions=universe(ranged(0, 0), ranged(1, 0), ranged(2, 0)),
                    changed={"0", "10", "20"},
                    max_bytes=250,
                    expected=[["0"], ["10"], ["20"]],
                ),
                id="partitions-with-no-size-are-runs-of-their-own",
            ),
            pytest.param(
                RunCase(
                    partitions=universe(ranged(0, 100), ranged(1, 0), ranged(2, 100)),
                    changed={"0", "10", "20"},
                    max_bytes=250,
                    expected=[["0"], ["10"], ["20"]],
                ),
                id="a-partition-with-no-size-splits-its-known-neighbors",
            ),
            pytest.param(
                RunCase(
                    partitions=universe(ranged(0, 0), ranged(1, 0), ranged(2, 0)),
                    changed={"0", "10", "20"},
                    max_bytes=None,
                    expected=[["0", "10", "20"]],
                ),
                id="no-limit-merges-partitions-with-no-size",
            ),
            pytest.param(
                RunCase(
                    partitions=universe(
                        ranged(0, 100), ranged(1, 100), ranged(-1, 100)
                    ),
                    changed={"0", "10", "-10"},
                    max_bytes=None,
                    expected=[["-10", "0", "10"]],
                ),
                id="a-negative-start-sorts-before-zero",
            ),
        ],
    )
    def test_groups_the_changed_partitions(self, case: RunCase) -> None:
        found = runs(case.partitions, case.changed, case.max_bytes)

        assert [list(run.partition_ids) for run in found] == case.expected

    def test_merges_a_range_run_into_one_range(self) -> None:
        found = runs(
            universe(ranged(0, 1), ranged(1, 1), ranged(2, 1)), {"0", "10", "20"}, None
        )

        assert [run.selection for run in found] == [
            RangeSelection(partition_id="0", column="cpf", lower=0, upper=30)
        ]

    def test_merges_a_time_run_into_one_time_range(self) -> None:
        found = runs(
            universe(daily(1, 1), daily(2, 1), daily(3, 1)),
            {"20260901", "20260902", "20260903"},
            None,
        )

        assert [run.selection for run in found] == [
            TimeRangeSelection(column="date", lower="2026-09-01", upper="2026-09-04")
        ]

    def test_keeps_the_selection_of_a_run_with_one_partition(self) -> None:
        partitions = universe(ranged(0, 1), ranged(1, 1))

        found = runs(partitions, {"0"}, None)

        assert [run.selection for run in found] == [partitions["0"].selection]

    def test_keeps_the_null_remainder_alone_and_last(self) -> None:
        remainder = partition(
            "__NULL__",
            RemainderSelection(column="cpf", start=0, end=30),
            logical_bytes=1,
        )
        partitions = universe(remainder, ranged(0, 1), ranged(1, 1))

        found = runs(partitions, {"__NULL__", "0", "10"}, None)

        assert [list(run.partition_ids) for run in found] == [["0", "10"], ["__NULL__"]]
        assert found[-1].selection == remainder.selection

    @pytest.mark.parametrize(
        "selections",
        [
            pytest.param(
                [
                    RangeSelection(partition_id="0", column="a", lower=0, upper=10),
                    RangeSelection(partition_id="10", column="b", lower=10, upper=20),
                ],
                id="range-columns-differ",
            ),
            pytest.param(
                [
                    RangeSelection(partition_id="0", column="a", lower=0, upper=10),
                    TimeRangeSelection(
                        column="a", lower="2026-01-01", upper="2026-01-02"
                    ),
                ],
                id="kinds-differ",
            ),
        ],
    )
    def test_never_merges_partitions_of_another_column_or_kind(
        self, selections: list[RangeSelection | TimeRangeSelection]
    ) -> None:
        partitions = universe(
            *(
                partition(str(index * 10), selection, logical_bytes=1)
                for index, selection in enumerate(selections)
            )
        )

        found = runs(partitions, set(partitions), None)

        assert [len(run.partition_ids) for run in found] == [1, 1]


class TestMerge:
    """A group merges into one selection when its partitions have one kind."""

    @pytest.mark.parametrize(
        "group",
        [
            pytest.param([ranged(0, 1), daily(1, 1)], id="range-then-time"),
            pytest.param([daily(1, 1), ranged(0, 1)], id="time-then-range"),
        ],
    )
    def test_refuses_partitions_of_different_kinds(
        self, group: list[PhysicalPartition]
    ) -> None:
        with pytest.raises(ValueError, match="different kinds"):
            merge(group)
