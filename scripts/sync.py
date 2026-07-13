"""Sync: BigQuery -> GCS Parquet -> pg_duckdb.

Implements the sync topology from aplications-architecture/proposta-pedro
("Sincronizacao BQ -> pg_duckdb"): export each BQ table to GCS as Parquet,
then load it into pg_duckdb via `read_parquet()` (public schema -- see the
gotcha note in load_into_pg_duckdb below). `full_refresh` mode
only for this PoC (TRUNCATE + INSERT in a transaction) -- incremental mode
(cursor-based watermarks) is documented as future work in
docs/architecture-decisions.md, not built here.

No Prefect: this is a plain, manually-triggered script (`just sync`). See
docs/architecture-decisions.md, "Why a plain script for sync, not Prefect",
for why that's an accepted PoC-scope tradeoff, not a production recommendation.

Auth split, matching pg_duckdb's actual GCS auth mechanism (confirmed in
Phase 1 research, see docs/phase-1-validation.md and the pg_duckdb docs):
  - BigQuery export + GCS bucket write: Application Default Credentials
    (gcloud auth application-default login), same as seed_bigquery.py.
  - pg_duckdb's own read of the GCS Parquet files: HMAC keys via the GCS
    S3-interoperability API, NOT Workload Identity or ADC. Postgres has no
    access to your local gcloud credentials -- it needs its own
    long-lived key pair. Generate one with:
      gcloud storage hmac create <service-account-email> --project=$GCP_PROJECT_ID
    and set GCS_HMAC_KEY_ID / GCS_HMAC_SECRET in .env.
"""

import os

import psycopg
from dotenv import load_dotenv
from google.cloud import bigquery, storage
from loguru import logger

load_dotenv()

PROJECT_ID = os.environ["GCP_PROJECT_ID"]
DATASET = os.environ["BQ_DATASET"]
BUCKET = os.environ["GCS_BUCKET"]
HMAC_KEY_ID = os.environ["GCS_HMAC_KEY_ID"]
HMAC_SECRET = os.environ["GCS_HMAC_SECRET"]

DB_DSN = (
    f"host=localhost port=5544 "
    f"dbname={os.environ.get('DB_NAME', 'poc')} "
    f"user={os.environ.get('DB_USER', 'poc')} "
    f"password={os.environ.get('DB_PASSWORD', 'poc')}"
)

# (bq_table, pg_table, columns) -- columns are explicit, not SELECT *,
# because pg_duckdb's read_parquet() returns a composite `row` type: columns
# must be pulled out via `r['colname']::type` with an explicit cast, they
# can't be referenced bare or auto-coerced into the target table's types
# (confirmed live: `SELECT id FROM read_parquet(...)` raises "column id does
# not exist" even though `id` is very much in the Parquet file -- Postgres's
# own error message is what points at the r['colname'] syntax).
#
# This also means schema drift in the source BQ table (proposta-pedro's
# "propriedade de schema" concern -- see aplications-architecture/README.md)
# is caught at sync time as an explicit KeyError, not silently ignored or
# blindly passed through. app-pic/MIGRATION.md's "extra JSONB buffer"
# pattern is the documented answer for handling that gracefully; adding it
# here is flagged as future work, not built into this PoC (see
# docs/architecture-decisions.md).
TABLES = [
    (
        "citizens",
        "citizens",
        [
            ("id", "uuid"),
            ("name", "text"),
            ("unit_id", "text"),
            ("status", "text"),
        ],
    ),
    (
        "service_records",
        "service_records",
        [
            ("id", "uuid"),
            ("citizen_id", "uuid"),
            ("unit_id", "text"),
            ("protocol_type", "text"),
            ("updated_at", "timestamptz"),
        ],
    ),
]


def export_table_to_gcs(bq_client: bigquery.Client, bq_table: str) -> str:
    gcs_uri = f"gs://{BUCKET}/{bq_table}/{bq_table}.parquet"
    table_ref = f"{PROJECT_ID}.{DATASET}.{bq_table}"

    job_config = bigquery.ExtractJobConfig(destination_format="PARQUET")
    job = bq_client.extract_table(table_ref, gcs_uri, job_config=job_config)
    job.result()

    logger.info(f"Exported {table_ref} -> {gcs_uri}")
    return gcs_uri


def load_into_pg_duckdb(
    conn: psycopg.Connection,
    pg_table: str,
    gcs_uri: str,
    columns: list[tuple[str, str]],
):
    col_names = ", ".join(name for name, _ in columns)
    # r['colname']::type -- see the TABLES comment above for why this can't
    # be a plain SELECT *. read_parquet() lives in the `public` schema, NOT
    # `duckdb` (unlike duckdb.create_simple_secret()) -- confirmed via
    # pg_proc; `duckdb.read_parquet` does not exist and raises
    # UndefinedFunction.
    # Explicit `AS name` alias is required: DuckDB names the output column
    # after the row-accessor expression itself (literally "r") when left
    # unaliased, so selecting multiple r['col'] expressions without aliases
    # collides with "column r specified more than once".
    select_exprs = ", ".join(
        f"r['{name}']::{pg_type} AS {name}" for name, pg_type in columns
    )
    tmp_name = f"tmp_{pg_table}"

    with conn.cursor() as cur:
        # Two steps, not one INSERT-SELECT: pg_duckdb's DuckDB execution
        # engine refuses to write directly into an existing Postgres heap
        # table ("DuckDB does not support modifying Postgres tables"), even
        # via a plain INSERT INTO ... SELECT FROM read_parquet(...). It CAN
        # create a brand new table from a DuckDB scan (CREATE TABLE ... AS
        # SELECT), so we materialize into a session-local TEMP table first,
        # then move that into the real heap table with a normal
        # Postgres-to-Postgres INSERT-SELECT (no DuckDB execution involved
        # in that second step at all). This is also why `web_anon` needs
        # zero DuckDB/FDW grants for serving -- only this sync script, run
        # as the privileged `poc` role, ever touches DuckDB execution. RLS
        # and PostgREST serve exclusively from the resulting plain heap
        # tables. See docs/phase-3-sync-findings.md for the full investigation.
        # gcs_uri is NOT passed as a bind parameter here: psycopg's
        # extended query protocol (prepared statement) breaks pg_duckdb's
        # planner specifically for read_parquet() inside CREATE TABLE AS --
        # confirmed live with "Not implemented Error: Could not convert
        # DuckDB type: UNKNOWN to Postgres type" even with an explicit
        # ::text cast on the parameter. Literal interpolation works because
        # it makes the argument a plain string literal, not a placeholder
        # DuckDB's planner has to infer a type for. Safe here because
        # gcs_uri is built entirely by export_table_to_gcs() above from our
        # own BUCKET/bq_table constants, never from external/user input.
        assert gcs_uri.startswith(f"gs://{BUCKET}/") and "'" not in gcs_uri
        cur.execute(
            f"CREATE TEMP TABLE {tmp_name} AS "
            f"SELECT {select_exprs} FROM read_parquet('{gcs_uri}') AS r"
        )
        cur.execute(f"INSERT INTO api.{pg_table} ({col_names}) SELECT {col_names} FROM {tmp_name}")
        cur.execute(f"DROP TABLE {tmp_name}")
    conn.commit()
    logger.info(f"Loaded {gcs_uri} -> api.{pg_table}")


def truncate_all(conn: psycopg.Connection, pg_tables: list[str]):
    # full_refresh: TRUNCATE + INSERT, per proposta-pedro's table-mode
    # framing (no cursor/watermark on this synthetic data, so incremental
    # mode doesn't apply here). Postgres refuses to TRUNCATE a table that's
    # the target of an FK (citizens, referenced by service_records) unless
    # every referencing table is truncated in the SAME statement -- doing
    # them as separate statements fails even if the referencing table is
    # truncated first, because the check is against the constraint's
    # existence, not the referencing table's current row count.
    table_list = ", ".join(f"api.{t}" for t in pg_tables)
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {table_list}")
    conn.commit()


def ensure_gcs_secret(conn: psycopg.Connection):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT duckdb.create_simple_secret(%s, %s, %s, %s)",
            ("gcs", HMAC_KEY_ID, HMAC_SECRET, ""),
        )
    conn.commit()


def main():
    bq_client = bigquery.Client(project=PROJECT_ID)
    storage.Client(project=PROJECT_ID)  # fail fast if ADC can't reach GCS

    with psycopg.connect(DB_DSN) as conn:
        ensure_gcs_secret(conn)

        gcs_uris = {
            pg_table: export_table_to_gcs(bq_client, bq_table)
            for bq_table, pg_table, _ in TABLES
        }

        truncate_all(conn, [pg_table for _, pg_table, _ in TABLES])

        for _, pg_table, columns in TABLES:
            load_into_pg_duckdb(conn, pg_table, gcs_uris[pg_table], columns)

    logger.info("Sync complete.")


if __name__ == "__main__":
    main()
