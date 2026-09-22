"""BigQuery physical partition query and normalization helpers."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import assert_never, cast

from google.cloud.bigquery import QueryJobConfig, ScalarQueryParameter
from google.cloud.bigquery.table import Row, Table
from whenever import PlainDateTime

from ..executor import execute_sql
from ..models import (
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)
from .clients import BigQuery
from .config import (
    PartitionKindConfig,
    RangeConfig,
    TimeConfig,
    TimeGranularity,
    partition_kind_config,
    partitioned_table_signature,
)


@dataclass(frozen=True, slots=True)
class TableReference:
    """Validated project, dataset, and table from a BigQuery reference."""

    project: str
    dataset: str
    table: str


@dataclass(frozen=True, slots=True)
class PartitionNormalizer:
    """Normalize BigQuery partition metadata rows into PhysicalPartition models."""

    kind_config: PartitionKindConfig
    table: str
    signature: str

    def normalize(self, row: Row) -> PhysicalPartition | None:
        """Normalize one BigQuery metadata row into partition state."""
        partition_id = cast("str", row["partition_id"])
        if partition_id == "__UNPARTITIONED__":
            msg = f"Unsupported BigQuery partition {partition_id}: {self.table}"
            raise ValueError(msg)

        modified = self.modified(row)
        if modified is None:
            msg = f"Missing partition modification time {partition_id}: {self.table}"
            raise TypeError(msg)

        partition_signature = sha256(
            f"{partition_id}:{modified.isoformat()}:{self.signature}".encode()
        ).hexdigest()

        logical_bytes = self.logical_bytes(row)

        match self.kind_config.kind:
            case "time":
                return self.normalize_time(
                    partition_id,
                    partition_signature,
                    cast("TimeConfig", self.kind_config),
                    logical_bytes,
                )
            case "range":
                return self.normalize_range(
                    partition_id,
                    partition_signature,
                    cast("RangeConfig", self.kind_config),
                    logical_bytes,
                )
            case _:
                assert_never(self.kind_config.kind)

    def modified(self, row: Row) -> datetime | None:
        """Extract last_modified_time from a BigQuery row."""
        return cast("datetime | None", row["last_modified_time"])

    @staticmethod
    def logical_bytes(row: Row) -> int:
        """Extract logical_bytes from a BigQuery row as an integer."""
        return cast("int | None", row["logical_bytes"]) or 0

    def normalize_time(
        self,
        partition_id: str,
        partition_signature: str,
        config: TimeConfig,
        logical_bytes: int,
    ) -> PhysicalPartition | None:
        """Normalize one time partition."""
        if partition_id == "__NULL__":
            return None

        lower, upper = self.time_bounds(partition_id, config.granularity)

        return PhysicalPartition(
            partition_id=partition_id,
            signature=partition_signature,
            selection=TimeRangeSelection(column=config.field, lower=lower, upper=upper),
            logical_bytes=logical_bytes,
        )

    def normalize_range(
        self,
        partition_id: str,
        partition_signature: str,
        config: RangeConfig,
        logical_bytes: int,
    ) -> PhysicalPartition:
        """Normalize one integer range partition."""
        if partition_id == "__NULL__":
            return PhysicalPartition(
                partition_id=partition_id,
                signature=partition_signature,
                selection=RemainderSelection(
                    column=config.field, start=config.start, end=config.end
                ),
                logical_bytes=logical_bytes,
            )

        try:
            lower = int(partition_id)
        except ValueError:
            lower = -1

        in_bounds = config.start <= lower < config.end
        aligned = (lower - config.start) % config.interval == 0

        if not in_bounds or not aligned:
            raise ValueError(f"Invalid range partition ID {partition_id}: {self.table}")

        upper = min(lower + config.interval, config.end)

        return PhysicalPartition(
            partition_id=partition_id,
            signature=partition_signature,
            selection=RangeSelection(
                partition_id=partition_id,
                column=config.field,
                lower=lower,
                upper=upper,
            ),
            logical_bytes=logical_bytes,
        )

    def time_bounds(
        self,
        partition_id: str,
        granularity: TimeGranularity,
    ) -> tuple[str, str]:
        """Return the [start, end) date/timestamp bounds one compact partition id covers."""
        spec = granularity.spec()

        try:
            parsed = datetime.strptime(partition_id, spec.strptime_format).replace(
                tzinfo=UTC
            )
        except ValueError as error:
            msg = f"Invalid time partition ID {partition_id}: {self.table}"
            raise ValueError(msg) from error

        start = PlainDateTime(parsed.year, parsed.month, parsed.day, parsed.hour)
        end = spec.step(start)

        return start.format(spec.output_pattern), end.format(spec.output_pattern)


async def table_modified(bq_conn: BigQuery, table: str) -> str:
    """Return the table modification time in epoch milliseconds."""
    metadata = await bq_conn.get_table(table)

    if metadata.modified is None:
        raise ValueError(f"Missing BigQuery modification time: {table}")

    return str(int(metadata.modified.timestamp() * 1000))


def parse_table_reference(table: str) -> TableReference:
    """Split a model-validated BigQuery table reference into its components."""
    project, dataset, table_name = table.split(".")
    return TableReference(project=project, dataset=dataset, table=table_name)


async def partition_rows(
    bq_conn: BigQuery,
    project: str,
    dataset: str,
    table_name: str,
) -> Iterable[Row]:
    """Return grouped physical partition metadata rows."""
    return await execute_sql(
        bq_conn,
        "bigquery/partitions",
        {"project": project, "dataset": dataset},
        job_config=QueryJobConfig(
            query_parameters=[ScalarQueryParameter("table_name", "STRING", table_name)]
        ),
    )


async def physical_partitions(
    bq_conn: BigQuery,
    table: str,
    config_json: str,
    n: int | None = None,
) -> tuple[str, dict[str, PhysicalPartition]]:
    """Return the table signature and current physical partitions."""
    reference = parse_table_reference(table)
    metadata: Table = await bq_conn.get_table(table)
    kind_cfg = partition_kind_config(metadata, table)

    match kind_cfg:
        case kind_cfg if kind_cfg.kind == "range" and n is not None:
            msg = f"n is only supported for time-partitioned tables: {table}"
            raise ValueError(msg)
        case _:
            pass

    signature = partitioned_table_signature(metadata, config_json, kind_cfg)
    normalizer = PartitionNormalizer(
        kind_config=kind_cfg, table=table, signature=signature
    )

    partitions: dict[str, PhysicalPartition] = {}

    for row in await partition_rows(
        bq_conn, reference.project, reference.dataset, reference.table
    ):
        partition = normalizer.normalize(row)

        if partition:
            partitions[partition.partition_id] = partition

    if n is not None:
        kept = sorted(partitions, reverse=True)[:n]
        partitions = {partition_id: partitions[partition_id] for partition_id in kept}

    return signature, partitions
