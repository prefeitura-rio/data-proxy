from hypothesis import strategies as st

from data_proxy.models import FullTable, PartitionedTable, TableConfig
from data_proxy.sources.partitions import PhysicalPartition, RangeSelection

identifiers = st.from_regex(r"[a-z][a-z0-9_]{0,8}", fullmatch=True)


def ordered_bounds() -> st.SearchStrategy[tuple[int, int]]:
    """Generate ordered integer bounds for unit tests."""
    return st.tuples(st.integers(-100, 100), st.integers(1, 100)).map(
        lambda values: (values[0], values[0] + values[1])
    )


@st.composite
def range_selections(draw: st.DrawFn) -> RangeSelection:
    """Generate one valid integer range selection."""
    lower, upper = draw(ordered_bounds())
    partition_id = draw(st.integers(0, 1000).map(str))
    return RangeSelection(
        partition_id=partition_id,
        column=draw(identifiers),
        lower=lower,
        upper=upper,
    )


@st.composite
def physical_partitions(draw: st.DrawFn) -> PhysicalPartition:
    """Generate one valid physical partition."""
    selection = draw(range_selections())
    return PhysicalPartition(
        partition_id=selection.partition_id,
        signature=draw(identifiers),
        selection=selection,
        logical_bytes=draw(st.integers(0, 1_000_000)),
    )


@st.composite
def table_configs(draw: st.DrawFn) -> TableConfig:
    """Generate one full or partitioned table configuration."""
    name = ".".join(draw(st.tuples(identifiers, identifiers, identifiers)))
    schema = draw(identifiers)
    if draw(st.booleans()):
        return FullTable(name=name, resolved_schema=schema)
    return PartitionedTable(name=name, resolved_schema=schema)
