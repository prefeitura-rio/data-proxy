"""Seed synthetic data into BigQuery for the data-proxy sync pipeline.

Creates two tables matching the sync config schema:

  endpoint_participante_listagem   — full strategy
  protocolo_estado_diario          — partitioned strategy (partitioned by date)

Idempotent: safe to re-run, tables are recreated each time.

Auth: uses Application Default Credentials. Set --project to override
the default GCP project.
"""

from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import environ
from pathlib import Path
from random import choice, randint
from random import seed as set_seed
from typing import cast
from uuid import uuid4

from google.cloud.bigquery import (
    Client,
    LoadJobConfig,
    SchemaField,
    TimePartitioning,
    TimePartitioningType,
    WriteDisposition,
)

from dp.log import logger
from dp.models import SyncConfig

type Scalar = str | None
type NestedValue = Scalar | dict[str, "NestedValue"]
type Row = dict[str, NestedValue]


DEFAULT_UNIDADES = ["cras_1", "cras_2", "cras_3", "cras_4", "cras_5"]
DEFAULT_ESTADOS = ["ATIVO", "INATIVO", "SUSPENSO"]


@dataclass
class Config:
    project: str | None
    dataset: str
    n_participantes: int
    protocolos_por_participante: tuple[int, int]
    partition_days: int
    seed: int
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
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--sync-config",
        type=Path,
        default=Path("config/sync.test.json"),
        help="Sync configuration that lists the development tables",
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
        seed=cast(int, args.seed),
        sync_config=cast(Path, args.sync_config),
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
                "indicadores": {
                    "status": choice(DEFAULT_ESTADOS),
                    "secretaria": choice(["smas", "sme", "sms"]),
                    "smas": {
                        "acesso_alimentacao": choice([None, "regular", "irregular"]),
                        "cadunico_atualizado": choice([None, "regular", "irregular"]),
                    },
                    "sme": {
                        "frequencia_escolar": choice([None, "regular", "irregular"]),
                        "matriculado_creche": choice([None, "sim", "nao"]),
                    },
                    "sms": {
                        "consultas_pre_natal": choice([None, "regular", "irregular"]),
                        "vacinacao_pentavalente": choice(
                            [None, "regular", "irregular"]
                        ),
                    },
                },
            }
        )
    return rows


def build_protocolos(
    n: int, max_days: int, protocolos_range: tuple[int, int]
) -> list[Row]:
    rows: list[Row] = []
    now = datetime.now(tz=UTC).date()
    for _ in range(n):
        n_prot = randint(*protocolos_range)
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
    table_ref: str,
    rows: list[Row],
    schema: list[SchemaField],
    time_partitioning: TimePartitioning | None = None,
) -> None:
    full_table_id = table_ref
    job_config = LoadJobConfig(
        schema=schema,
        time_partitioning=time_partitioning,
        write_disposition=WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_json(rows, full_table_id, job_config=job_config)
    job.result()
    logger.info("Rows loaded rows=%d table=%s", len(rows), full_table_id)


def main() -> None:
    cfg = parse_args()

    set_seed(cfg.seed)

    client = Client(project=cfg.project) if cfg.project else Client()
    sync_config = SyncConfig.model_validate_json(cfg.sync_config.read_text())
    table_refs = {table.name for table in sync_config.tables}
    required_tables = {
        "rj-ia-desenvolvimento.dev.endpoint_participante_listagem",
        "rj-ia-desenvolvimento.dev.protocolo_estado_diario",
        "rj-ia-desenvolvimento.dev.endpoint_participantes",
    }
    missing_tables = required_tables - table_refs
    if missing_tables:
        raise ValueError(
            f"sync config is missing development tables: {sorted(missing_tables)}"
        )

    def table_ref(table_name: str) -> str:
        configured = next(
            table.name for table in sync_config.tables if table.table_name == table_name
        )
        if cfg.project:
            return f"{cfg.project}.{configured.split('.', 1)[1]}"
        return configured

    client.create_dataset(cfg.dataset, exists_ok=True)

    participantes = build_participantes(cfg.n_participantes)
    load_table(
        client,
        table_ref("endpoint_participante_listagem"),
        participantes,
        schema=[
            SchemaField("id", "STRING", mode="REQUIRED"),
            SchemaField("nome", "STRING", mode="REQUIRED"),
            SchemaField("cpf", "STRING", mode="REQUIRED"),
            SchemaField("data_nascimento", "DATE", mode="REQUIRED"),
            SchemaField("id_unidade", "STRING", mode="REQUIRED"),
            SchemaField(
                "indicadores",
                "RECORD",
                mode="NULLABLE",
                fields=[
                    SchemaField("status", "STRING", mode="NULLABLE"),
                    SchemaField("secretaria", "STRING", mode="NULLABLE"),
                    SchemaField(
                        "smas",
                        "RECORD",
                        mode="NULLABLE",
                        fields=[
                            SchemaField(
                                "acesso_alimentacao", "STRING", mode="NULLABLE"
                            ),
                            SchemaField(
                                "cadunico_atualizado", "STRING", mode="NULLABLE"
                            ),
                        ],
                    ),
                    SchemaField(
                        "sme",
                        "RECORD",
                        mode="NULLABLE",
                        fields=[
                            SchemaField(
                                "frequencia_escolar", "STRING", mode="NULLABLE"
                            ),
                            SchemaField(
                                "matriculado_creche", "STRING", mode="NULLABLE"
                            ),
                        ],
                    ),
                    SchemaField(
                        "sms",
                        "RECORD",
                        mode="NULLABLE",
                        fields=[
                            SchemaField(
                                "consultas_pre_natal", "STRING", mode="NULLABLE"
                            ),
                            SchemaField(
                                "vacinacao_pentavalente", "STRING", mode="NULLABLE"
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    protocolos = build_protocolos(
        cfg.n_participantes,
        cfg.partition_days,
        cfg.protocolos_por_participante,
    )
    load_table(
        client,
        table_ref("protocolo_estado_diario"),
        protocolos,
        schema=[
            SchemaField("protocolo_id", "STRING", mode="REQUIRED"),
            SchemaField(
                "protocolo_data_referencia_particicao", "DATE", mode="REQUIRED"
            ),
            SchemaField("estado", "STRING", mode="REQUIRED"),
            SchemaField("id_unidade", "STRING", mode="REQUIRED"),
        ],
        time_partitioning=TimePartitioning(
            type_=TimePartitioningType.DAY,
            field="protocolo_data_referencia_particicao",
        ),
    )

    participantes_extra: list[Row] = [
        {
            "id": str(i + 1),
            "nome": f"Participante {uuid4().hex[:8]}",
            "id_cras": choice(DEFAULT_UNIDADES),
            "id_escola": f"escola_{randint(1, 5)}",
        }
        for i in range(cfg.n_participantes)
    ]
    load_table(
        client,
        table_ref("endpoint_participantes"),
        participantes_extra,
        schema=[
            SchemaField("id", "STRING", mode="REQUIRED"),
            SchemaField("nome", "STRING", mode="REQUIRED"),
            SchemaField("id_cras", "STRING", mode="REQUIRED"),
            SchemaField("id_escola", "STRING", mode="REQUIRED"),
        ],
    )

    logger.info(
        "Seed completed participantes=%d protocolos=%d units=%d",
        len(participantes),
        len(protocolos),
        len(DEFAULT_UNIDADES),
    )


if __name__ == "__main__":
    main()
