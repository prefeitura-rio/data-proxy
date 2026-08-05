"""Seed synthetic data into BigQuery for the data-proxy sync pipeline.

Creates two tables matching the sync config schema:

  endpoint_participante_listagem   — dump strategy
  protocolo_estado_diario          — window strategy (partitioned by date)

Idempotent: safe to re-run, tables are recreated each time.

Auth: uses Application Default Credentials. Set --project to override
the default GCP project.
"""

import sys
from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import environ
from random import choice, randint
from random import seed as set_seed
from typing import cast
from uuid import uuid4

from google.cloud.bigquery import (
    Client,
    LoadJobConfig,
    SchemaField,
    WriteDisposition,
)
from loguru import logger

logger.remove()
logger.add(sys.stderr, format="[{level}] {message}")

type Row = dict[str, str]

DEFAULT_UNIDADES = ["cras_1", "cras_2", "cras_3", "cras_4", "cras_5"]
DEFAULT_ESTADOS = ["ATIVO", "INATIVO", "SUSPENSO"]


@dataclass
class Config:
    project: str | None
    dataset: str
    n_participantes: int
    protocolos_por_participante: tuple[int, int]
    partition_days: int
    seed: int | None


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
        help="BigQuery dataset for both tables",
    )
    parser.add_argument(
        "--n-participantes",
        type=int,
        default=500,
        help="Number of participants to generate",
    )
    parser.add_argument(
        "--protocolos-por-participante",
        type=int,
        nargs=2,
        default=[1, 3],
        metavar=("MIN", "MAX"),
        help="Range of protocolos per participant",
    )
    parser.add_argument(
        "--partition-days",
        type=int,
        default=7,
        help="Number of partition days for the window table",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )

    args = parser.parse_args()

    return Config(
        project=cast("str | None", args.project),
        dataset=cast(str, args.dataset),
        n_participantes=cast(int, args.n_participantes),
        protocolos_por_participante=cast(
            "tuple[int, int]",
            tuple(cast("list[int]", args.protocolos_por_participante)),
        ),
        partition_days=cast(int, args.partition_days),
        seed=cast("int | None", args.seed),
    )


def build_participantes(n: int) -> list[Row]:
    rows: list[Row] = []
    for i in range(n):
        birth = datetime.now(tz=UTC).date() - timedelta(days=randint(365, 365 * 18))
        rows.append(
            {
                "id": str(i + 1),
                "nome": f"Participante {uuid4().hex[:8]}",
                "cpf": (
                    f"{randint(100, 999):03d}"
                    f"{randint(100, 999):03d}"
                    f"{randint(100, 999):03d}"
                    f"{randint(10, 99):02d}"
                ),
                "data_nascimento": birth.isoformat(),
                "id_unidade": choice(DEFAULT_UNIDADES),
            }
        )
    return rows


def build_protocolos(n: int, max_days: int) -> list[Row]:
    rows: list[Row] = []
    now = datetime.now(tz=UTC).date()
    for _ in range(n):
        n_prot = randint(1, 3)
        for _ in range(n_prot):
            ref_date = now - timedelta(days=randint(0, max_days - 1))
            rows.append(
                {
                    "protocolo_id": f"P-{uuid4().hex[:12]}",
                    "protocolo_data_referencia_particicao": ref_date.isoformat(),
                    "estado": choice(DEFAULT_ESTADOS),
                    "id_unidade": choice(DEFAULT_UNIDADES),
                }
            )
    return rows


def load_table(
    client: Client,
    project: str | None,
    dataset: str,
    table_id: str,
    rows: list[Row],
    schema: list[SchemaField],
) -> None:
    full_table_id = (
        f"{project}.{dataset}.{table_id}" if project else f"{dataset}.{table_id}"
    )
    job_config = LoadJobConfig(
        schema=schema,
        write_disposition=WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_json(rows, full_table_id, job_config=job_config)
    job.result()
    logger.info("Loaded {} rows into {}", len(rows), full_table_id)


def main() -> None:
    cfg = parse_args()

    if cfg.seed is not None:
        set_seed(cfg.seed)

    client = Client(project=cfg.project) if cfg.project else Client()

    client.create_dataset(cfg.dataset, exists_ok=True)

    participantes = build_participantes(cfg.n_participantes)
    load_table(
        client,
        cfg.project,
        cfg.dataset,
        "endpoint_participante_listagem",
        participantes,
        schema=[
            SchemaField("id", "STRING", mode="REQUIRED"),
            SchemaField("nome", "STRING", mode="REQUIRED"),
            SchemaField("cpf", "STRING", mode="REQUIRED"),
            SchemaField("data_nascimento", "DATE", mode="REQUIRED"),
            SchemaField("id_unidade", "STRING", mode="REQUIRED"),
        ],
    )

    protocolos = build_protocolos(cfg.n_participantes, cfg.partition_days)
    load_table(
        client,
        cfg.project,
        cfg.dataset,
        "protocolo_estado_diario",
        protocolos,
        schema=[
            SchemaField("protocolo_id", "STRING", mode="REQUIRED"),
            SchemaField(
                "protocolo_data_referencia_particicao", "DATE", mode="REQUIRED"
            ),
            SchemaField("estado", "STRING", mode="REQUIRED"),
            SchemaField("id_unidade", "STRING", mode="REQUIRED"),
        ],
    )

    logger.info(
        "Done. {} participantes, {} protocolos across {} units.",
        len(participantes),
        len(protocolos),
        len(DEFAULT_UNIDADES),
    )


if __name__ == "__main__":
    main()
