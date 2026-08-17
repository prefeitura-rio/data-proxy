"""BigQuery metadata helpers for synchronization change detection."""

import json
import re
from collections.abc import Iterable
from datetime import datetime
from hashlib import sha256
from typing import cast

from google.cloud.bigquery import Client, QueryJobConfig, ScalarQueryParameter, Table
from google.cloud.bigquery.table import Row

from .constants import BIGQUERY_TABLE_REFERENCE_PATTERN
from .models import PhysicalPartition
from .templates import load_template


def table_modified(client: Client, bq_table: str) -> str:
    """Return the table modification time in epoch milliseconds."""
    modified = client.get_table(bq_table).modified

    if modified is None:
        msg = f"Missing BigQuery modification time: {bq_table}"
        raise ValueError(msg)

    return str(int(modified.timestamp() * 1000))


def parse_table_reference(bq_table: str) -> tuple[str, str, str]:
    """Return a validated project, dataset, and table reference."""
    match = re.fullmatch(BIGQUERY_TABLE_REFERENCE_PATTERN, bq_table)

    if match is None:
        msg = f"Invalid BigQuery table reference: {bq_table}"
        raise ValueError(msg)

    return (
        match.group("project"),
        match.group("dataset"),
        match.group("table"),
    )


def range_config(metadata: Table, bq_table: str) -> tuple[str, int, int, int]:
    """Return validated integer-range configuration from table metadata."""
    partitioning = metadata.range_partitioning

    if metadata.table_type != "TABLE" or partitioning is None:
        msg = f"all_with_partitions requires a range-partitioned table: {bq_table}"
        raise ValueError(msg)

    match (
        partitioning.field,
        partitioning.range_.start,
        partitioning.range_.end,
        partitioning.range_.interval,
    ):
        case str(field), int(start), int(end), int(interval):
            pass
        case _:
            msg = f"Incomplete range partition metadata: {bq_table}"
            raise ValueError(msg)

    invalid_interval = interval <= 0
    invalid_bounds = start >= end

    if invalid_interval or invalid_bounds:
        msg = f"Invalid range partition metadata: {bq_table}"
        raise ValueError(msg)

    return field, start, end, interval


def partitioned_table_signature(
    metadata: Table,
    config_json: str,
    field: str,
    start: int,
    end: int,
    interval: int,
) -> str:
    """Hash source schema, range metadata, and synchronization configuration."""
    return sha256(
        json.dumps(
            {
                "config": config_json,
                "field": field,
                "start": start,
                "end": end,
                "interval": interval,
                "schema": repr(cast(object, metadata.schema)),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def partition_rows(
    client: Client,
    project: str,
    dataset: str,
    table_name: str,
) -> Iterable[Row]:
    """Return grouped physical partition metadata rows."""
    query = load_template(
        {
            "path": "bigquery/partitions",
            "mapping": {"project": project, "dataset": dataset},
        }
    )
    job_config = QueryJobConfig(
        query_parameters=[ScalarQueryParameter("table_name", "STRING", table_name)]
    )
    result: object = client.query(query, job_config=job_config).result()
    return cast("Iterable[Row]", result)


def normalize_partition(
    row: Row,
    bq_table: str,
    field: str,
    start: int,
    end: int,
    interval: int,
    table_signature: str,
) -> PhysicalPartition:
    """Normalize one BigQuery metadata row into bounded partition state."""
    partition_id_value = cast(object, row["partition_id"])
    partition_id = str(partition_id_value)

    if partition_id in {"__NULL__", "__UNPARTITIONED__"}:
        msg = f"Unsupported BigQuery partition {partition_id}: {bq_table}"
        raise ValueError(msg)

    try:
        lower = int(partition_id)
    except ValueError as error:
        msg = f"Invalid range partition ID {partition_id}: {bq_table}"
        raise ValueError(msg) from error

    upper = min(lower + interval, end)
    before_range = lower < start
    after_range = lower >= end
    misaligned = (lower - start) % interval != 0
    empty_range = lower >= upper

    if before_range or after_range or misaligned or empty_range:
        msg = f"Invalid range partition ID {partition_id}: {bq_table}"
        raise ValueError(msg)

    modified_value = cast(object, row["last_modified_time"])

    match modified_value:
        case datetime() as modified:
            pass
        case _:
            msg = f"Missing partition modification time {partition_id}: {bq_table}"
            raise TypeError(msg)

    signature = sha256(
        f"{partition_id}:{modified.isoformat()}:{table_signature}".encode()
    ).hexdigest()

    return PhysicalPartition(
        partition_id=partition_id,
        column=field,
        lower=lower,
        upper=upper,
        signature=signature,
    )


def physical_partitions(
    client: Client,
    bq_table: str,
    config_json: str,
) -> tuple[str, dict[str, PhysicalPartition]]:
    """Return the table signature and existing integer-range partitions."""
    project, dataset, table_name = parse_table_reference(bq_table)
    metadata = client.get_table(bq_table)
    field, start, end, interval = range_config(metadata, bq_table)
    table_signature = partitioned_table_signature(
        metadata, config_json, field, start, end, interval
    )
    normalized = (
        normalize_partition(
            row,
            bq_table,
            field,
            start,
            end,
            interval,
            table_signature,
        )
        for row in partition_rows(client, project, dataset, table_name)
    )
    partitions = {partition.partition_id: partition for partition in normalized}
    return table_signature, partitions
