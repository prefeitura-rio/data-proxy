"""BigQuery metadata helpers for synchronization change detection."""

import json
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import assert_never, cast

from google.cloud.bigquery import (
    Client,
    QueryJobConfig,
    RangePartitioning,
    ScalarQueryParameter,
    TimePartitioning,
)
from google.cloud.bigquery.table import Row
from whenever import PlainDateTime

from .models import (
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from .protocols import BigQueryMetadataClient, BigQueryTable
from .templates import TemplateSpec, load_template


@dataclass(frozen=True, slots=True)
class TableReference:
    """Validated project, dataset, and table from a BigQuery reference."""

    project: str
    dataset: str
    table: str


@dataclass(slots=True)
class RangeConfig:
    """Validated integer-range partition configuration for one table."""

    field: str
    start: int
    end: int
    interval: int

    def __post_init__(self) -> None:
        """Require usable field and range metadata."""
        if not self.field:
            raise ValueError("Range partition field must not be empty")
        if self.interval <= 0:
            raise ValueError("Range partition interval must be positive")
        if self.start >= self.end:
            raise ValueError("Range partition start must precede end")


class TimeGranularity(StrEnum):
    """Supported BigQuery time partition granularities."""

    HOUR = "HOUR"
    DAY = "DAY"
    MONTH = "MONTH"
    YEAR = "YEAR"


@dataclass(frozen=True, slots=True)
class TimePartitionSpec:
    """How to parse and step through one BigQuery time-partition granularity."""

    strptime_format: str
    step: Callable[[PlainDateTime], PlainDateTime]
    output_pattern: str


@dataclass(slots=True)
class TimeConfig:
    """Validated time-partition configuration for one table."""

    field: str
    granularity: TimeGranularity

    def __post_init__(self) -> None:
        """Require usable time partition metadata."""
        if not self.field:
            raise ValueError("Time partition field must not be empty")


PartitionKindConfig = RangeConfig | TimeConfig


@contextmanager
def bigquery_clients() -> Generator[Callable[[str], BigQueryMetadataClient]]:
    """Yield a per-project BigQuery client getter, closing every client on exit."""
    clients: dict[str, Client] = {}

    def get_client(project: str) -> BigQueryMetadataClient:
        client = clients.get(project)

        if client is None:
            client = Client(project=project)
            clients[project] = client

        return cast(BigQueryMetadataClient, cast(object, client))

    try:
        yield get_client

    finally:
        for client in clients.values():
            client.close()


def table_modified(client: BigQueryMetadataClient, table: str) -> str:
    """Return the table modification time in epoch milliseconds."""
    modified = client.get_table(table).modified

    if modified is None:
        msg = f"Missing BigQuery modification time: {table}"
        raise ValueError(msg)

    return str(int(modified.timestamp() * 1000))


def parse_table_reference(table: str) -> TableReference:
    """Split a model-validated BigQuery table reference into its components."""
    project, dataset, table_name = table.split(".")
    return TableReference(project=project, dataset=dataset, table=table_name)


def range_config(partitioning: RangePartitioning, table: str) -> RangeConfig:
    """Return validated integer-range configuration from range metadata."""
    match (
        partitioning.field,
        partitioning.range_.start,
        partitioning.range_.end,
        partitioning.range_.interval,
    ):
        case str(field), int(start), int(end), int(interval):
            pass
        case _:
            msg = f"Incomplete range partition metadata: {table}"
            raise ValueError(msg)

    try:
        return RangeConfig(field=field, start=start, end=end, interval=interval)
    except ValueError as error:
        msg = f"Invalid range partition metadata: {table}"
        raise ValueError(msg) from error


def time_config(partitioning: TimePartitioning, table: str) -> TimeConfig:
    """Return validated time-partition configuration from time metadata."""
    field = partitioning.field

    if field is None:
        msg = f"Ingestion-time partitioning without an explicit field is unsupported: {table}"
        raise ValueError(msg)

    assert isinstance(field, str)
    raw_granularity = partitioning.type_ or TimeGranularity.DAY

    try:
        return TimeConfig(field=field, granularity=TimeGranularity(raw_granularity))
    except ValueError as error:
        msg = f"Unsupported time partition granularity {raw_granularity}: {table}"
        raise ValueError(msg) from error


def partition_kind_config(metadata: BigQueryTable, table: str) -> PartitionKindConfig:
    """Detect and return the time or range partition configuration."""
    if metadata.table_type != "TABLE":
        msg = f"partitioned requires a physically partitioned table: {table}"
        raise ValueError(msg)

    if metadata.range_partitioning is not None:
        return range_config(metadata.range_partitioning, table)

    if metadata.time_partitioning is not None:
        return time_config(metadata.time_partitioning, table)

    msg = f"partitioned requires a time- or range-partitioned table: {table}"
    raise ValueError(msg)


def partitioned_table_signature(
    metadata: BigQueryTable,
    config_json: str,
    kind_config: PartitionKindConfig,
) -> str:
    """Hash source schema, partition metadata, and synchronization configuration."""
    return sha256(
        json.dumps(
            {
                "config": config_json,
                **asdict(kind_config),
                "schema": repr(cast(object, metadata.schema)),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def partition_rows(
    client: BigQueryMetadataClient,
    project: str,
    dataset: str,
    table_name: str,
) -> Iterable[Row]:
    """Return grouped physical partition metadata rows."""
    query = load_template(
        TemplateSpec(
            path="bigquery/partitions",
            mapping={"project": project, "dataset": dataset},
        )
    )

    result = client.query(
        query,
        job_config=(
            QueryJobConfig(
                query_parameters=[
                    ScalarQueryParameter("table_name", "STRING", table_name)
                ]
            )
        ),
    ).result()

    return cast("Iterable[Row]", result)


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


def normalize_partition(
    row: Row,
    table: str,
    kind_config: PartitionKindConfig,
    table_signature: str,
) -> PhysicalPartition | None:
    """Normalize one BigQuery metadata row into partition state.

    Time partitions skip BigQuery's ``__NULL__`` bucket; range partitions
    normalize it into a ``RemainderSelection`` instead of dropping it.
    ``__UNPARTITIONED__`` is always rejected.
    """
    partition_id_value = cast(object, row["partition_id"])
    partition_id = str(partition_id_value)

    if partition_id == "__UNPARTITIONED__":
        msg = f"Unsupported BigQuery partition {partition_id}: {table}"
        raise ValueError(msg)

    modified_value = cast(object, row["last_modified_time"])

    match modified_value:
        case datetime() as modified:
            pass
        case _:
            msg = f"Missing partition modification time {partition_id}: {table}"
            raise TypeError(msg)

    signature = sha256(
        f"{partition_id}:{modified.isoformat()}:{table_signature}".encode()
    ).hexdigest()

    match kind_config:
        case TimeConfig(field=field, granularity=granularity):
            if partition_id == "__NULL__":
                return None

            lower, upper = time_partition_bounds(partition_id, granularity, table)

            return PhysicalPartition(
                partition_id=partition_id,
                signature=signature,
                selection=TimeRangeSelection(column=field, lower=lower, upper=upper),
            )
        case RangeConfig(field=field, start=start, end=end, interval=interval):
            if partition_id == "__NULL__":
                return PhysicalPartition(
                    partition_id=partition_id,
                    signature=signature,
                    selection=RemainderSelection(column=field, start=start, end=end),
                )

            try:
                lower = int(partition_id)
            except ValueError as error:
                msg = f"Invalid range partition ID {partition_id}: {table}"
                raise ValueError(msg) from error

            upper = min(lower + interval, end)
            before_range = lower < start
            after_range = lower >= end
            misaligned = (lower - start) % interval != 0
            empty_range = lower >= upper

            if before_range or after_range or misaligned or empty_range:
                msg = f"Invalid range partition ID {partition_id}: {table}"
                raise ValueError(msg)

            return PhysicalPartition(
                partition_id=partition_id,
                signature=signature,
                selection=RangeSelection(
                    partition_id=partition_id, column=field, lower=lower, upper=upper
                ),
            )
        case _:
            assert_never(kind_config)


def physical_partitions(
    client: BigQueryMetadataClient,
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
