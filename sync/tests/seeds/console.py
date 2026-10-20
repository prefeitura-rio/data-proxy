"""Seed the test catalog and its table files function for the console tests."""

from os import environ

import duckdb
from psycopg import connect
from psycopg.sql import Identifier, Literal

from sync.ducklake import DuckLakePaths
from sync.settings import settings
from sync.templates import render_template

SCHEMA = "test"

RUN_ID = "00000000-0000-4000-8000-0000000000f1"

paths = DuckLakePaths.for_schema(SCHEMA)

db = duckdb.connect()

for extension in ("httpfs", "ducklake", "postgres_scanner"):
    db.install_extension(extension)

db.execute(
    render_template(
        "duckdb/setup",
        {
            "s3_key_id": Literal(settings.S3_ACCESS_KEY),
            "s3_secret_key": Literal(settings.S3_SECRET_KEY),
            "s3_endpoint": Literal(settings.S3_ENDPOINT),
            "s3_use_ssl": "true" if settings.S3_USE_SSL else "false",
            "source_extensions": [],
        },
    )
)

db.execute(
    render_template(
        "duckdb/attach",
        {
            "catalog": Literal(paths.catalog),
            "data_path": Literal(paths.data),
            "metadata_schema": Literal(paths.metadata_schema),
            "encrypted": False,
        },
    )
)

db.execute(
    "CREATE TABLE dl.full_table AS SELECT i AS n, 'unit_' || (i % 5 + 1) AS unit_id FROM range(5000) AS t(i)"
)

db.execute("DELETE FROM dl.full_table WHERE n < 100")

db.execute(
    f"COPY (SELECT count(*) FROM dl.full_table) TO '{environ['EXPECTED_ROWS_FILE']}' (FORMAT csv, HEADER false)"
)

with connect(settings.PG_DATABASE_URL, autocommit=True) as connection:
    connection.execute(
        render_template(
            "postgres/create_table_files_function",
            {
                "schema": Identifier(SCHEMA),
                "function": Identifier("dl_table_files"),
                "metadata_schema": SCHEMA,
                "data_path": paths.data,
            },
        )
    )

    connection.execute(
        """
        WITH runs AS (
            SELECT dbos.enqueue_workflow(
                workflow_name => 'run_sync',
                queue_name => 'sync',
                positional_args => ARRAY[
                    to_json(now()), json_build_object('schemas', ARRAY['test'], 'tables', ARRAY[]::text[])
                ],
                authenticated_user => 'seed'
            ) AS id
            FROM generate_series(1, 30)
        )
        INSERT INTO data_proxy.scope (workflow_id, run_id, schema_name)
        SELECT id, id, 'test' FROM runs
        """
    )
    connection.execute(
        """
        WITH numbered AS (
            SELECT workflow_uuid, row_number() OVER (ORDER BY created_at, workflow_uuid) AS n
            FROM dbos.workflow_status
            WHERE authenticated_user = 'seed'
        )
        UPDATE dbos.workflow_status AS w
        SET status = CASE numbered.n % 3 WHEN 0 THEN 'SUCCESS' ELSE 'ERROR' END
        FROM numbered
        WHERE w.workflow_uuid = numbered.workflow_uuid AND numbered.n % 3 <> 2
        """
    )
    connection.execute(
        """
        INSERT INTO test.access_policy (subject, unit_type, unit_id)
        SELECT 'seed-subject-' || lpad(n::text, 2, '0'), 'cpf', '9000' || lpad(n::text, 2, '0')
        FROM generate_series(1, 60) AS n
        """
    )
    connection.execute(
        """
        INSERT INTO data_proxy.errors (reason, fields)
        SELECT 'seed error ' || n, jsonb_build_object('schema', 'test', 'table', 'full_table')
        FROM generate_series(1, 15) AS n
        """
    )
    connection.execute(
        """
        WITH base AS (SELECT (extract(epoch FROM now()) * 1000)::bigint - 3600000 AS ms)
        INSERT INTO dbos.workflow_status (
            workflow_uuid, status, name, authenticated_user, queue_name, queue_partition_key,
            parent_workflow_id, created_at, updated_at, started_at_epoch_ms, completed_at
        )
        SELECT %(run)s, 'SUCCESS', 'run_sync', 'seed-run', 'sync', NULL, NULL, ms, ms + 3000000, ms, ms + 3000000
        FROM base
        UNION ALL
        SELECT
            %(run)s || '-dump-' || lpad(n::text, 2, '0'), CASE WHEN n = 60 THEN 'ERROR' ELSE 'SUCCESS' END,
            'dump_task', 'seed-run', 'dump', 'test', %(run)s, ms + n * 1000, ms + n * 1000 + 500,
            ms + n * 1000, ms + n * 1000 + 500
        FROM base, generate_series(1, 60) AS n
        UNION ALL
        SELECT
            %(run)s || '-publish', 'SUCCESS', 'publish_schema', 'seed-run', 'publish', 'test', %(run)s,
            ms + 70000, ms + 71000, ms + 70000, ms + 71000
        FROM base
        """,
        {"run": RUN_ID},
    )
    connection.execute(
        "INSERT INTO data_proxy.scope (workflow_id, run_id, schema_name) VALUES (%s, %s, 'test')",
        (RUN_ID, RUN_ID),
    )
    connection.execute(
        """
        WITH base AS (SELECT (extract(epoch FROM now()) * 1000)::bigint - 3600000 AS ms)
        INSERT INTO dbos.operation_outputs (
            workflow_uuid, function_id, function_name, error, started_at_epoch_ms, completed_at_epoch_ms
        )
        SELECT %(run)s, n, (ARRAY['run_provision', 'load_sync_config', 'build_sync_work', 'record_scope'])[n],
            NULL, ms + n * 100, ms + n * 100 + 50
        FROM base, generate_series(1, 4) AS n
        UNION ALL
        SELECT %(run)s || '-dump-' || lpad(n::text, 2, '0'), 1, 'extract_task',
            CASE WHEN n = 60 THEN 'seed failure' END, ms + n * 1000, ms + n * 1000 + 500
        FROM base, generate_series(1, 60) AS n
        """,
        {"run": RUN_ID},
    )
    connection.execute(
        """
        INSERT INTO dbos.streams (workflow_uuid, key, value, "offset", serialization)
        SELECT
            %s, 'logs',
            json_build_object(
                'time', now() - (600 - n) * interval '1 second',
                'level', CASE WHEN mod(n, 100) = 0 THEN 'ERROR' ELSE 'INFO' END,
                'message', 'seed log line ' || n,
                'fields', '{}'::json,
                'schema', 'test',
                'table', 'full_table'
            )::text,
            n, 'portable_json'
        FROM generate_series(0, 599) AS n
        """,
        (RUN_ID,),
    )
