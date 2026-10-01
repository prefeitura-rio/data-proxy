"""Seed synthetic BigQuery data for the data-proxy sync service."""

import tomllib
from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import environ
from pathlib import Path
from random import choice, randint
from random import seed as set_seed
from typing import ClassVar, cast
from uuid import uuid4

from google.cloud.bigquery import (
    Client,
    LoadJobConfig,
    SchemaField,
    Table,
    TimePartitioning,
    TimePartitioningType,
    WriteDisposition,
)
from pydantic import BaseModel, ConfigDict, PositiveInt

from data_proxy.log import logger
from data_proxy.models import SyncConfig

type Scalar = str | None
type NestedValue = Scalar | dict[str, "NestedValue"]
type Row = dict[str, NestedValue]


FULL_SCHEMA = [
    SchemaField("id", "STRING", mode="REQUIRED"),
    SchemaField("name", "STRING", mode="REQUIRED"),
    SchemaField("unit_id", "STRING", mode="REQUIRED"),
    SchemaField(
        "metadata",
        "RECORD",
        mode="NULLABLE",
        fields=[
            SchemaField("status", "STRING", mode="NULLABLE"),
            SchemaField("tags", "STRING", mode="NULLABLE"),
        ],
    ),
]
UNIT_IDS = ["unit_1", "unit_2", "unit_3", "unit_4", "unit_5"]
REGION_IDS = ["region_1", "region_2", "region_3"]
GROUP_IDS = ["group_1", "group_2", "group_3"]
STATUSES = ["active", "inactive", "pending"]


class SeedModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class TableSeed(SeedModel):
    rows: PositiveInt


class PartitionedTableSeed(TableSeed):
    partition_days: PositiveInt


class TableSeeds(SeedModel):
    full_table: TableSeed
    partitioned_table: PartitionedTableSeed
    multi_rls_table: TableSeed
    big_table: PartitionedTableSeed


class SeedSettings(SeedModel):
    seed: int
    tables: TableSeeds


@dataclass
class Config:
    project: str | None
    dataset: str
    settings: SeedSettings
    sync_config: Path


def parse_args() -> Config:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=environ.get("GCP_PROJECT_ID"),
        help="GCP project ID (default: ADC project or env GCP_PROJECT_ID)",
    )
    parser.add_argument(
        "--dataset",
        default=environ.get("BQ_DATASET", "dev"),
        help="BigQuery dataset for all tables",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/seed.toml"),
        help="TOML file with the seed, partition days, and row count of each table",
    )
    parser.add_argument(
        "--sync-config",
        type=Path,
        default=Path("config/sync.test.json"),
        help="Sync configuration that lists the test tables",
    )

    args = parser.parse_args()
    config_path = cast(Path, args.config)

    return Config(
        project=cast("str | None", args.project),
        dataset=cast(str, args.dataset),
        settings=SeedSettings.model_validate(tomllib.loads(config_path.read_text())),
        sync_config=cast(Path, args.sync_config),
    )


def build_full_table_rows(n: int) -> list[Row]:
    return [
        {
            "id": str(i + 1),
            "name": f"Row {uuid4().hex[:8]}",
            "unit_id": choice(UNIT_IDS),
            "metadata": {
                "status": choice(STATUSES),
                "tags": choice([None, "alpha", "beta", "gamma"]),
            },
        }
        for i in range(n)
    ]


def build_big_table_rows(n: int, max_days: int) -> list[Row]:
    today = datetime.now(tz=UTC).date()
    return [
        {**row, "date": (today - timedelta(days=randint(0, max_days - 1))).isoformat()}
        for row in build_full_table_rows(n)
    ]


def build_multi_rls_table_rows(n: int) -> list[Row]:
    return [
        {
            "id": str(i + 1),
            "name": f"Row {uuid4().hex[:8]}",
            "region_id": choice(REGION_IDS),
            "group_id": choice(GROUP_IDS),
        }
        for i in range(n)
    ]


def build_partitioned_table_rows(n: int, max_days: int) -> list[Row]:
    rows: list[Row] = []
    now = datetime.now(tz=UTC).date()
    for i in range(n):
        ref_date = now - timedelta(days=randint(0, max_days - 1))
        rows.append(
            {
                "id": str(i + 1),
                "date": ref_date.isoformat(),
                "status": choice(STATUSES),
                "unit_id": choice(UNIT_IDS),
            }
        )
    return rows


def load_table(
    client: Client,
    table_ref: str,
    rows: list[Row],
    schema: list[SchemaField],
    time_partitioning: TimePartitioning | None = None,
) -> None:
    job_config = LoadJobConfig(
        schema=schema,
        time_partitioning=time_partitioning,
        write_disposition=WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_json(rows, table_ref, job_config=job_config)
    job.result()
    logger.info("Rows loaded rows=%d table=%s", len(rows), table_ref)


def main() -> None:
    cfg = parse_args()

    set_seed(cfg.settings.seed)

    client = Client(project=cfg.project) if cfg.project else Client()
    sync_config = SyncConfig.model_validate_json(cfg.sync_config.read_text())
    table_refs = {table.name for table in sync_config.tables}
    required_tables = {
        "rj-ia-desenvolvimento.dev.full_table",
        "rj-ia-desenvolvimento.dev.partitioned_table",
        "rj-ia-desenvolvimento.dev.multi_rls_table",
        "rj-ia-desenvolvimento.dev.failing_table",
        "rj-ia-desenvolvimento.dev.big_table",
    }
    missing_tables = required_tables - table_refs
    if missing_tables:
        raise ValueError(
            f"sync config is missing test tables: {sorted(missing_tables)}"
        )

    def table_ref(table_name: str) -> str:
        configured = next(
            table.name for table in sync_config.tables if table.table_name == table_name
        )
        if cfg.project:
            return f"{cfg.project}.{configured.split('.', 1)[1]}"
        return configured

    client.create_dataset(cfg.dataset, exists_ok=True)

    tables = cfg.settings.tables
    full_rows = build_full_table_rows(tables.full_table.rows)
    load_table(
        client,
        table_ref("full_table"),
        full_rows,
        schema=FULL_SCHEMA,
    )

    big_ref = table_ref("big_table")
    client.delete_table(big_ref, not_found_ok=True)
    big_rows = build_big_table_rows(
        tables.big_table.rows, tables.big_table.partition_days
    )
    load_table(
        client,
        big_ref,
        big_rows,
        schema=[*FULL_SCHEMA, SchemaField("date", "DATE", mode="REQUIRED")],
        time_partitioning=TimePartitioning(
            type_=TimePartitioningType.DAY,
            field="date",
        ),
    )

    partitioned_rows = build_partitioned_table_rows(
        tables.partitioned_table.rows, tables.partitioned_table.partition_days
    )
    load_table(
        client,
        table_ref("partitioned_table"),
        partitioned_rows,
        schema=[
            SchemaField("id", "STRING", mode="REQUIRED"),
            SchemaField("date", "DATE", mode="REQUIRED"),
            SchemaField("status", "STRING", mode="REQUIRED"),
            SchemaField("unit_id", "STRING", mode="REQUIRED"),
        ],
        time_partitioning=TimePartitioning(
            type_=TimePartitioningType.DAY,
            field="date",
        ),
    )

    multi_rls_rows = build_multi_rls_table_rows(tables.multi_rls_table.rows)
    load_table(
        client,
        table_ref("multi_rls_table"),
        multi_rls_rows,
        schema=[
            SchemaField("id", "STRING", mode="REQUIRED"),
            SchemaField("name", "STRING", mode="REQUIRED"),
            SchemaField("region_id", "STRING", mode="REQUIRED"),
            SchemaField("group_id", "STRING", mode="REQUIRED"),
        ],
    )

    failing_ref = table_ref("failing_table")
    client.delete_table(failing_ref, not_found_ok=True)
    failing = Table(
        failing_ref,
        schema=[
            SchemaField("id", "STRING", mode="REQUIRED"),
            SchemaField("date", "DATE", mode="REQUIRED"),
            SchemaField("unit_id", "STRING", mode="REQUIRED"),
        ],
    )
    failing.time_partitioning = TimePartitioning(
        type_=TimePartitioningType.DAY,
        field="date",
    )
    failing.require_partition_filter = True
    client.create_table(failing)
    client.query(
        f"INSERT INTO `{failing_ref}` VALUES ('1', CURRENT_DATE(), 'unit_1')"
    ).result()

    logger.info(
        "Seed completed full=%d partitioned=%d multi_rls=%d big=%d",
        len(full_rows),
        len(partitioned_rows),
        len(multi_rls_rows),
        len(big_rows),
    )


if __name__ == "__main__":
    main()
