"""BigQuery physical partition query and normalization helpers."""

from collections.abc import Iterable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol, assert_never

from google.cloud.bigquery import Client, QueryJobConfig, ScalarQueryParameter
from google.cloud.bigquery.table import Row
from whenever import PlainDateTime

from ..models import (
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from ..templates import render_template
from .config import (
    PartitionKindConfig,
    RangeConfig,
    TimeConfig,
    TimeGranularity,
    TimePartitionSpec,
    partition_kind_config,
    partitioned_table_signature,
)
from .tables import parse_table_reference


class TypedRow(Protocol):
    """Typed interface for BigQuery Row untyped subscript access."""

    def __getitem__(self, key: str) -> object: ...


def row_partition_id(row: TypedRow) -> str:
    """Extract partition_id from a BigQuery row as a string."""
    return str(row["partition_id"])


def row_modified(row: TypedRow) -> datetime | None:
    """Extract last_modified_time from a BigQuery row as a datetime."""
    value = row["last_modified_time"]

    if isinstance(value, datetime):
        return value

    return None


def partition_rows(
    client: Client,
    project: str,
    dataset: str,
    table_name: str,
) -> Iterable[Row]:
    """Return grouped physical partition metadata rows."""
    query = render_template(
        path="bigquery/partitions", mapping={"project": project, "dataset": dataset}
    )

    return client.query(
        query,
        job_config=(
            QueryJobConfig(
                query_parameters=[
                    ScalarQueryParameter("table_name", "STRING", table_name)
                ]
            )
        ),
    ).result()


def add_hour(dt: PlainDateTime) -> PlainDateTime:
    """Step one HOUR-granularity time partition forward."""
    return dt.add(hours=1, naive_arithmetic_ok=True)


def add_day(dt: PlainDateTime) -> PlainDateTime:
    """Step one DAY-granularity time partition forward."""
    return dt.add(days=1, naive_arithmetic_ok=True)


def add_month(dt: PlainDateTime) -> PlainDateTime:
    """Step one MONTH-granularity time partition forward."""
    return dt.add(months=1, naive_arithmetic_ok=True)


def add_year(dt: PlainDateTime) -> PlainDateTime:
    """Step one YEAR-granularity time partition forward."""
    return dt.add(years=1, naive_arithmetic_ok=True)


TIME_PARTITION_SPECS: dict[TimeGranularity, TimePartitionSpec] = {
    TimeGranularity.HOUR: TimePartitionSpec(
        "%Y%m%d%H", add_hour, "YYYY-MM-DD hh:mm:ss"
    ),
    TimeGranularity.DAY: TimePartitionSpec("%Y%m%d", add_day, "YYYY-MM-DD"),
    TimeGranularity.MONTH: TimePartitionSpec("%Y%m", add_month, "YYYY-MM-DD"),
    TimeGranularity.YEAR: TimePartitionSpec("%Y", add_year, "YYYY-MM-DD"),
}


def time_partition_bounds(
    partition_id: str,
    granularity: TimeGranularity,
    table: str,
) -> tuple[str, str]:
    """Return the [start, end) date/timestamp bounds one compact partition id covers."""
    spec = TIME_PARTITION_SPECS[granularity]

    try:
        parsed = datetime.strptime(partition_id, spec.strptime_format).replace(
            tzinfo=UTC
        )
    except ValueError as error:
        msg = f"Invalid time partition ID {partition_id}: {table}"
        raise ValueError(msg) from error

    start = PlainDateTime(parsed.year, parsed.month, parsed.day, parsed.hour)
    end = spec.step(start)

    return start.format(spec.output_pattern), end.format(spec.output_pattern)


def normalize_time_partition(
    partition_id: str,
    signature: str,
    table: str,
    config: TimeConfig,
) -> PhysicalPartition | None:
    """Normalize one time partition"""
    if partition_id == "__NULL__":
        return None

    lower, upper = time_partition_bounds(partition_id, config.granularity, table)

    return PhysicalPartition(
        partition_id=partition_id,
        signature=signature,
        selection=TimeRangeSelection(column=config.field, lower=lower, upper=upper),
    )


def normalize_range_partition(
    partition_id: str,
    signature: str,
    table: str,
    config: RangeConfig,
) -> PhysicalPartition:
    """Normalize one integer range partition"""
    if partition_id == "__NULL__":
        return PhysicalPartition(
            partition_id=partition_id,
            signature=signature,
            selection=RemainderSelection(
                column=config.field, start=config.start, end=config.end
            ),
        )

    try:
        lower = int(partition_id)
    except ValueError as error:
        msg = f"Invalid range partition ID {partition_id}: {table}"
        raise ValueError(msg) from error

    in_bounds = config.start <= lower < config.end
    aligned = (lower - config.start) % config.interval == 0

    if not in_bounds or not aligned:
        msg = f"Invalid range partition ID {partition_id}: {table}"
        raise ValueError(msg)

    upper = min(lower + config.interval, config.end)

    return PhysicalPartition(
        partition_id=partition_id,
        signature=signature,
        selection=RangeSelection(
            partition_id=partition_id,
            column=config.field,
            lower=lower,
            upper=upper,
        ),
    )


def normalize_partition(
    row: TypedRow,
    table: str,
    kind_config: PartitionKindConfig,
    table_signature: str,
) -> PhysicalPartition | None:
    """Normalize one BigQuery metadata row into partition state.

    Time partitions skip BigQuery's ``__NULL__`` bucket; range partitions
    normalize it into a ``RemainderSelection`` instead of dropping it.
    ``__UNPARTITIONED__`` is always rejected.
    """
    partition_id = row_partition_id(row)

    if partition_id == "__UNPARTITIONED__":
        msg = f"Unsupported BigQuery partition {partition_id}: {table}"
        raise ValueError(msg)

    modified = row_modified(row)
    if modified is None:
        msg = f"Missing partition modification time {partition_id}: {table}"
        raise TypeError(msg)

    signature = sha256(
        f"{partition_id}:{modified.isoformat()}:{table_signature}".encode()
    ).hexdigest()

    match kind_config:
        case TimeConfig() as config:
            return normalize_time_partition(partition_id, signature, table, config)
        case RangeConfig() as config:
            return normalize_range_partition(partition_id, signature, table, config)
        case _:
            assert_never(kind_config)


def physical_partitions(
    client: Client,
    table: str,
    config_json: str,
    n: int | None = None,
) -> tuple[str, dict[str, PhysicalPartition]]:
    """Return the table signature and current physical partitions.

    ``n`` keeps only the last ``n`` time partitions (highest partition ids)
    and is only valid for time-partitioned tables.
    """
    reference = parse_table_reference(table)
    metadata = client.get_table(table)
    kind_cfg = partition_kind_config(metadata, table)

    match kind_cfg:
        case RangeConfig() if n is not None:
            msg = f"n is only supported for time-partitioned tables: {table}"
            raise ValueError(msg)
        case _:
            pass

    signature = partitioned_table_signature(metadata, config_json, kind_cfg)

    partitions: dict[str, PhysicalPartition] = {}

    for row in partition_rows(
        client, reference.project, reference.dataset, reference.table
    ):
        partition = normalize_partition(row, table, kind_cfg, signature)

        if partition is not None:
            partitions[partition.partition_id] = partition

    if n is not None:
        kept = sorted(partitions, reverse=True)[:n]
        partitions = {partition_id: partitions[partition_id] for partition_id in kept}

    return signature, partitions
