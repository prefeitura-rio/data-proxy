"""Seed the synthetic PoC dataset into BigQuery.

Creates two tables in `{GCP_PROJECT_ID}.{BQ_DATASET}` analogous to a real gov
analytical app's data model (see docs/architecture-decisions.md, "Why a
synthetic dataset"):

  citizens          -- id, name, unit_id, status
  service_records   -- id, citizen_id, unit_id, protocol_type, updated_at

`unit_id` on both tables is the field row-level access control filters on
(see the README's "Row-level access control" section) -- analogous to
app-pic's CRAS/secretaria governance model.

Idempotent: safe to re-run, tables are recreated each time (this is
synthetic PoC data, not anything with a retention requirement).

Auth: uses Application Default Credentials. Run `gcloud auth
application-default login` once as gabriel.milan@prefeitura.rio before
running this script.
"""

import os
import random
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from google.cloud import bigquery
from loguru import logger

load_dotenv()

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
DATASET = os.environ["BQ_DATASET"]

UNITS = ["cras_1", "cras_2", "cras_3", "cras_4", "cras_5"]
STATUSES = ["ativo", "inativo"]
PROTOCOL_TYPES = ["vacinacao", "cadunico", "frequencia_escolar", "creche"]

N_CITIZENS = 500
RECORDS_PER_CITIZEN_RANGE = (1, 4)


def build_citizens() -> list[dict]:
    rows = []
    for _ in range(N_CITIZENS):
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "name": f"Cidadao {uuid.uuid4().hex[:8]}",
                "unit_id": random.choice(UNITS),
                "status": random.choice(STATUSES),
            }
        )
    return rows


def build_service_records(citizens: list[dict]) -> list[dict]:
    rows = []
    now = datetime.now(timezone.utc)
    for citizen in citizens:
        n_records = random.randint(*RECORDS_PER_CITIZEN_RANGE)
        for _ in range(n_records):
            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "citizen_id": citizen["id"],
                    "unit_id": citizen["unit_id"],
                    "protocol_type": random.choice(PROTOCOL_TYPES),
                    "updated_at": (
                        now - timedelta(days=random.randint(0, 365))
                    ).isoformat(),
                }
            )
    return rows


def load_table(client: bigquery.Client, table_id: str, rows: list[dict], schema):
    full_table_id = f"{PROJECT_ID}.{DATASET}.{table_id}"
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_json(rows, full_table_id, job_config=job_config)
    job.result()
    logger.info(f"Loaded {len(rows)} rows into {full_table_id}")


def main():
    client = bigquery.Client(project=PROJECT_ID)

    citizens = build_citizens()
    service_records = build_service_records(citizens)

    load_table(
        client,
        "citizens",
        citizens,
        schema=[
            bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("unit_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
        ],
    )

    load_table(
        client,
        "service_records",
        service_records,
        schema=[
            bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("citizen_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("unit_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("protocol_type", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
        ],
    )

    logger.info(
        f"Done. {len(citizens)} citizens, {len(service_records)} service_records "
        f"across {len(UNITS)} units."
    )


if __name__ == "__main__":
    main()
