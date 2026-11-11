"""Group contiguous changed partitions into runs, so one query and one file cover many partitions."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .sources.partitions import (
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
    partition_sort_key,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from .sources.partitions import PhysicalPartition


type PartitionSelection = RangeSelection | TimeRangeSelection | RemainderSelection


@dataclass(frozen=True, slots=True)
class Run:
    """The ids of the contiguous partitions that one selection covers, and that selection."""

    partition_ids: tuple[str, ...]
    selection: PartitionSelection


def can_join(first: PhysicalPartition, other: PhysicalPartition) -> bool:
    """Tell whether another partition may follow the first one of a run, which needs the same kind and column."""
    match first.selection, other.selection:
        case RangeSelection(column=left), RangeSelection(column=right):
            return left == right
        case TimeRangeSelection(column=left), TimeRangeSelection(column=right):
            return left == right
        case _:
            return False


def merge(group: list[PhysicalPartition]) -> PartitionSelection:
    """Return one selection from the first lower bound to the last upper bound of a group."""
    first = group[0].selection
    last = group[-1].selection

    match first, last:
        case RangeSelection(), RangeSelection():
            return RangeSelection(
                partition_id=first.partition_id,
                column=first.column,
                lower=first.lower,
                upper=last.upper,
            )
        case TimeRangeSelection(), TimeRangeSelection():
            return TimeRangeSelection(
                column=first.column, lower=first.lower, upper=last.upper
            )
        case _:
            msg = f"Cannot merge partitions of different kinds: {first}, {last}"
            raise ValueError(msg)


def weight(item: PhysicalPartition, max_bytes: int | None) -> int:
    """Return the size that counts toward a run: a partition with no reported size fills the whole run, because its real size is unknown."""
    if item.logical_bytes == 0 and max_bytes is not None:
        return max_bytes

    return item.logical_bytes


@dataclass(slots=True)
class Builder:
    """The partitions of the run that is open, and their logical size."""

    group: list[PhysicalPartition] = field(default_factory=list)
    size: int = 0

    def accepts(self, item: PhysicalPartition, max_bytes: int | None) -> bool:
        """Tell whether the item may join the open run, which an empty run always allows."""
        if len(self.group) == 0:
            return True

        fits = max_bytes is None or self.size + weight(item, max_bytes) <= max_bytes

        return fits and can_join(self.group[0], item)

    def add(self, item: PhysicalPartition, max_bytes: int | None) -> None:
        """Put the item in the open run."""
        self.group.append(item)
        self.size += weight(item, max_bytes)

    def close(self) -> Run | None:
        """Return the open run and start a new one, or return None when the open run is empty."""
        group = self.group
        self.group = []
        self.size = 0

        if len(group) == 0:
            return None

        return Run(
            tuple(item.partition_id for item in group),
            group[0].selection if len(group) == 1 else merge(group),
        )


def runs(
    partitions: Mapping[str, PhysicalPartition],
    changed: Collection[str],
    max_bytes: int | None,
) -> list[Run]:
    """Group the changed partitions into runs, in the order of their key.

    A partition that did not change ends a run, so a run never covers rows that the sync does not replace.
    A gap with no partition does not end a run, because no rows live there. A run also ends at max_bytes of
    logical size, and a partition that is larger than max_bytes, or that has no reported size, is a run of its own. A null remainder is always
    alone. With max_bytes set to None a run has no size limit.
    """
    closed: list[Run | None] = []
    builder = Builder()

    for partition_id in sorted(partitions, key=partition_sort_key):
        item = partitions[partition_id]

        if partition_id not in changed or not builder.accepts(item, max_bytes):
            closed.append(builder.close())

        if partition_id in changed:
            builder.add(item, max_bytes)

        if isinstance(item.selection, RemainderSelection):
            closed.append(builder.close())

    closed.append(builder.close())

    return [run for run in closed if run is not None]
