"""Tests for the maintenance, the schema and the reconcile templates."""

import json
from time import time
from typing import Final

import pytest
from psycopg.sql import SQL, Identifier, Literal

from sync.models import (
    FullTable,
    SchemaConfig,
    SyncConfig,
    TableConfig,
)
from sync.postgres import Postgres as PgBackend
from sync.settings import settings
from sync.templates import render_template
from sync.views import stages
from sync.views.reconcile import reconcile_views
from tests.constants import (
    TEST_SQL_DIR,
)
from tests.fixtures.types import Postgres, Psql
from tests.helpers import (
    create_state_tables,
    execute_sql,
    fetch_all,
    function_exists,
    helm_sql,
    initialize_schemas,
    install_rbac,
    install_table_function,
    psql_script,
    reconcile_one_table,
    relation_exists,
    scalar,
    schema_scope_condition,
)

HOUR_MS: Final = 3_600_000


@pytest.mark.postgres
class TestMaintenanceTemplates:
    """The templates that clean up stale objects, prune old rows and recover workflows."""

    async def test_cleanup_stale_objects_drops_only_views_missing_from_the_config(
        self, postgres: Postgres, psql: Psql
    ) -> None:
        """Drops only views missing from the config, with their table and aggregate functions."""
        schema = postgres.namespace.schema
        target = postgres.namespace.identifier
        psql.run(
            psql_script(
                helm_sql("cleanup_stale_objects", {"schema": target}),
                render_template(
                    "postgres/create_view",
                    {"schema": target, "view": Identifier("kept")},
                    root=TEST_SQL_DIR,
                ),
                render_template(
                    "postgres/create_view",
                    {"schema": target, "view": Identifier("stale")},
                    root=TEST_SQL_DIR,
                ),
                render_template(
                    "postgres/create_stub_function",
                    {"schema": target, "function": Identifier("stale_fn")},
                    root=TEST_SQL_DIR,
                ),
                render_template(
                    "postgres/create_stub_function",
                    {"schema": target, "function": Identifier("stale_aggregate")},
                    root=TEST_SQL_DIR,
                ),
                render_template(
                    "postgres/create_stub_function",
                    {"schema": target, "function": Identifier("kept_aggregate")},
                    root=TEST_SQL_DIR,
                ),
                helm_sql(
                    "call_cleanup_stale_objects",
                    {"schema": target, "schema_argument": Literal(schema)},
                ),
            ),
            {
                "config": json.dumps(
                    {"schemas": {schema: {"tables": [{"name": f"p.{schema}.kept"}]}}}
                )
            },
        )

        assert [
            await relation_exists(postgres, schema, "kept"),
            await relation_exists(postgres, schema, "stale"),
            await function_exists(postgres, schema, "stale_fn()"),
            await function_exists(postgres, schema, "stale_aggregate()"),
            await function_exists(postgres, schema, "kept_aggregate()"),
        ] == [True, False, False, False, True]

    async def test_prune_access_log_keeps_only_recent_rows(
        self, postgres: Postgres, psql: Psql
    ) -> None:
        """Keeps only recent rows."""
        schema = postgres.namespace.schema
        target = postgres.namespace.identifier
        psql.run(
            psql_script(
                helm_sql(
                    "setup_access_policy",
                    {
                        "schema": target,
                        "user_role": Identifier(settings.AUTH_USER_ROLE),
                        "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
                        "scope": schema_scope_condition(schema),
                    },
                ),
                helm_sql("prune_access_log", {"schema": target}),
            )
        )
        for subject, age in (("stale", "100 days"), ("recent", "1 day")):
            await execute_sql(
                postgres,
                "postgres/insert_access_log",
                mapping={"schema": target},
                params={
                    "subject": subject,
                    "unit_type": "unit",
                    "unit_id": "unit_1",
                    "action": "insert",
                    "age": age,
                },
            )
        await postgres.connection.commit()

        psql.run(
            psql_script(helm_sql("call_prune_access_log", {"schema": target})),
            {"retention": "90 days", "schema": schema},
        )

        assert await fetch_all(
            postgres,
            "postgres/access_log_entries",
            mapping={"schema": target},
        ) == [("recent", "unit", "unit_1", "insert")]

    @pytest.mark.parametrize(
        ("status", "age_days", "kept"),
        [
            pytest.param("SUCCESS", 100, False, id="old-success"),
            pytest.param("ERROR", 100, False, id="old-error"),
            pytest.param("CANCELLED", 100, False, id="old-cancelled"),
            pytest.param(
                "MAX_RECOVERY_ATTEMPTS_EXCEEDED", 100, False, id="old-max-attempts"
            ),
            pytest.param("SUCCESS", 1, True, id="recent-success"),
            pytest.param("PENDING", 100, True, id="old-pending"),
            pytest.param("ENQUEUED", 100, True, id="old-enqueued"),
        ],
    )
    async def test_prune_workflows_deletes_only_old_finished_workflows(
        self,
        postgres: Postgres,
        psql: Psql,
        dbos_schema: str,
        status: str,
        age_days: int,
        kept: bool,
    ) -> None:
        """Deletes a finished workflow older than the retention with its scope rows, and keeps the others."""
        target = postgres.namespace.identifier
        dbos = {"dbos_schema": Identifier(dbos_schema)}
        workflow = f"{postgres.namespace.schema}-workflow"
        try:
            await postgres.connection.execute(
                helm_sql("create_state", {"schema": target}).encode()
            )
            await execute_sql(
                postgres,
                "postgres/insert_aged_workflow",
                mapping=dbos,
                params={
                    "workflow_uuid": workflow,
                    "status": status,
                    "updated_at": int(time() * 1000) - age_days * 24 * HOUR_MS,
                },
            )
            await postgres.connection.execute(
                SQL(
                    "INSERT INTO {}.scope (workflow_id, run_id, schema_name) VALUES (%s, 'r1', 's1')"
                ).format(target),
                (workflow,),
            )
            await postgres.connection.commit()

            psql.run(
                psql_script(
                    helm_sql("prune_workflows", {"schema": target}),
                    helm_sql("call_prune_workflows", {"schema": target}),
                ),
                {"retention": "90 days"},
            )

            assert await fetch_all(
                postgres,
                "postgres/select_workflows",
                mapping=dbos,
                params={"prefix": workflow},
            ) == ([(workflow, status, True)] if kept else [])
            assert await scalar(
                postgres,
                SQL("SELECT count(*) FROM {}.scope").format(target).as_string(),
            ) == (1 if kept else 0)
        finally:
            await postgres.connection.rollback()
            await execute_sql(
                postgres,
                "postgres/delete_workflows",
                mapping=dbos,
                params={"prefix": workflow},
            )
            await postgres.connection.commit()

    async def test_recover_orphaned_workflows_re_enqueues_only_orphaned_workflows(
        self, postgres: Postgres, psql: Psql, dbos_schema: str
    ) -> None:
        """Re enqueues only orphaned workflows."""
        schema = postgres.namespace.schema
        dbos = {"dbos_schema": Identifier(dbos_schema)}
        prefix = f"{schema}-"
        now_ms = int(time() * 1000)
        workflows = {
            "orphaned": (schema, "q", "gone", HOUR_MS),
            "live-executor": (schema, "q", "live", HOUR_MS),
            "within-grace": (schema, "q", "gone", 0),
            "other-application": (f"{schema}_other", "q", "gone", HOUR_MS),
            "not-queued": (schema, None, "gone", HOUR_MS),
        }
        try:
            for name, (application, queue, executor, age_ms) in workflows.items():
                await execute_sql(
                    postgres,
                    "postgres/insert_workflow",
                    mapping=dbos,
                    params={
                        "workflow_uuid": f"{prefix}{name}",
                        "application_name": application,
                        "queue_name": queue,
                        "executor_id": executor,
                        "started_at_epoch_ms": now_ms - age_ms,
                    },
                )
            await postgres.connection.commit()

            psql.run(
                psql_script(
                    helm_sql(
                        "recover_orphaned_workflows",
                        {
                            "schema": postgres.namespace.identifier,
                            "application_name": Literal(schema),
                        },
                    ),
                    helm_sql(
                        "call_recover_orphaned_workflows",
                        {
                            "schema": postgres.namespace.identifier,
                            "live_executor_ids": Literal(json.dumps(["live"])),
                            "grace_seconds": "60",
                        },
                    ),
                )
            )

            assert await fetch_all(
                postgres,
                "postgres/select_workflows",
                mapping=dbos,
                params={"prefix": prefix},
            ) == [
                (f"{prefix}live-executor", "PENDING", False),
                (f"{prefix}not-queued", "PENDING", False),
                (f"{prefix}orphaned", "ENQUEUED", True),
                (f"{prefix}other-application", "PENDING", False),
                (f"{prefix}within-grace", "PENDING", False),
            ]
        finally:
            await postgres.connection.rollback()
            await execute_sql(
                postgres,
                "postgres/delete_workflows",
                mapping=dbos,
                params={"prefix": prefix},
            )
            await postgres.connection.commit()


@pytest.mark.postgres
class TestSchemaLifecycle:
    """The templates that create and remove the schemas and the state tables."""

    async def test_create_state_makes_the_state_error_and_scope_tables(
        self, postgres: Postgres
    ) -> None:
        """Create the three tables of the engine, and create them again without error."""
        statement = helm_sql(
            "create_state", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
        ).encode()

        await postgres.connection.execute(statement)
        await postgres.connection.execute(statement)

        assert (
            await scalar(
                postgres,
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s AND table_name IN ('state', 'errors', 'scope')",
                settings.DBOS_APP_SCHEMA,
            )
            == 3
        )

    async def test_create_schema_makes_the_schema_once(
        self, postgres: Postgres
    ) -> None:
        """Create a schema, and create it again without error."""
        name = f"{postgres.namespace.schema}_new"
        statement = helm_sql("create_schema", {"schema": Identifier(name)}).encode()

        await postgres.connection.execute(statement)
        await postgres.connection.execute(statement)

        assert (
            await scalar(
                postgres, "SELECT count(*) FROM pg_namespace WHERE nspname = %s", name
            )
            == 1
        )

    async def test_remove_schema_drops_the_schema_and_what_the_engine_kept_for_it(
        self, postgres: Postgres
    ) -> None:
        """The state, the scope and the errors of a removed schema go with it, and the other schemas keep theirs."""
        name = f"{postgres.namespace.schema}_gone"
        app = Identifier(settings.DBOS_APP_SCHEMA)
        await create_state_tables(postgres.connection)
        await postgres.connection.execute(
            SQL(
                """
                CREATE SCHEMA {gone};
                CREATE VIEW {gone}.people AS SELECT 1 AS id;
                CREATE TABLE {gone}.access_policy (subject text);
                INSERT INTO {app}.state VALUES
                    ('p.d.people', '{{}}'), ('p.d.other', '{{}}');
                INSERT INTO {app}.scope VALUES
                    ('w1', 'r1', {name}, ''), ('w2', 'r2', 'kept', '');
                INSERT INTO {app}.errors (reason, fields) VALUES
                    ('table_blocked', '{{"table": "p.d.people"}}'),
                    ('table_blocked', '{{"table": "p.d.other"}}')
                """
            ).format(gone=Identifier(name), app=app, name=Literal(name))
        )
        statement = helm_sql(
            "remove_schema",
            {"schema": Identifier(name), "name": Literal(name), "app_schema": app},
        ).encode()

        await postgres.connection.execute(statement)
        await postgres.connection.execute(statement)

        assert (
            await scalar(
                postgres, "SELECT count(*) FROM pg_namespace WHERE nspname = %s", name
            )
            == 0
        )
        cursor = await postgres.connection.execute(
            SQL(
                """
                SELECT
                    (SELECT array_agg(table_name) FROM {app}.state
                     WHERE table_name IN ('p.d.people', 'p.d.other')),
                    (SELECT array_agg(schema_name) FROM {app}.scope
                     WHERE workflow_id IN ('w1', 'w2')),
                    (SELECT array_agg(fields ->> 'table') FROM {app}.errors
                     WHERE fields ->> 'table' IN ('p.d.people', 'p.d.other'))
                """
            ).format(app=app)
        )
        assert await cursor.fetchone() == (["p.d.other"], ["kept"], ["p.d.other"])

    async def test_drop_catalog_schema_drops_the_metadata_schema_and_is_repeatable(
        self, postgres: Postgres
    ) -> None:
        """The catalog database keeps one metadata schema for each application schema."""
        name = f"{postgres.namespace.schema}_catalog"
        await postgres.connection.execute(
            SQL(
                "CREATE SCHEMA {}; CREATE TABLE {}.ducklake_snapshot (id integer)"
            ).format(Identifier(name), Identifier(name))
        )
        statement = helm_sql(
            "drop_catalog_schema", {"schema": Identifier(name)}
        ).encode()

        await postgres.connection.execute(statement)
        await postgres.connection.execute(statement)

        assert (
            await scalar(
                postgres, "SELECT count(*) FROM pg_namespace WHERE nspname = %s", name
            )
            == 0
        )


@pytest.mark.postgres
class TestInstalledObjects:
    """The functions and views that the reconcile installs for each table."""

    async def test_every_security_definer_function_pins_its_search_path(
        self, table_function: Postgres
    ) -> None:
        """A function that runs with its owner rights must not trust the search_path of the caller."""
        await install_table_function(
            table_function, has_rls=True, fallbacks=["bigquery"]
        )
        await install_rbac(table_function)
        for name in (
            "create_state",
            "create_reload",
            "create_pre_request",
            "cleanup_stale_objects",
            "prune_access_log",
            "prune_workflows",
            "recover_orphaned_workflows",
        ):
            await table_function.connection.execute(
                helm_sql(
                    name,
                    {
                        "schema": Identifier(settings.DBOS_APP_SCHEMA),
                        "application_name": Literal("app"),
                    },
                ).encode()
            )

        cursor = await table_function.connection.execute(
            """
            SELECT p.oid::regprocedure::text
            FROM pg_proc AS p
            INNER JOIN pg_namespace AS n ON p.pronamespace = n.oid
            WHERE n.nspname = ANY(%s) AND p.prosecdef AND NOT EXISTS (
                SELECT FROM unnest(coalesce(p.proconfig, '{}')) AS c
                WHERE c LIKE 'search_path=pg_catalog%%'
            )
            """,
            ([table_function.namespace.schema, settings.DBOS_APP_SCHEMA, "rbac"],),
        )

        assert await cursor.fetchall() == []

    async def test_reconcile_creates_the_view_and_every_managed_function(
        self, postgres: Postgres, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One DuckLake table gets its view, its helpers, and the routing functions."""
        await reconcile_one_table(postgres, monkeypatch, [("cpf", "BIGINT")])
        schema = postgres.namespace.schema

        assert [
            await relation_exists(postgres, schema, "people"),
            await function_exists(postgres, schema, "people_fn()"),
            await function_exists(
                postgres, schema, "people_dl_fn(text, text, boolean, text[], text)"
            ),
            await function_exists(
                postgres, schema, "dl_changes_people(bigint, bigint)"
            ),
            await function_exists(postgres, schema, "dl_latest_snapshot()"),
            await function_exists(
                postgres,
                settings.DBOS_APP_SCHEMA,
                "plan_sources(text, text[], boolean)",
            ),
            await function_exists(postgres, schema, "people_bq_fn(text, text)"),
        ] == [True, True, True, True, True, True, False]

    async def test_reconcile_creates_the_bigquery_helper_of_a_fallback(
        self, postgres: Postgres, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A table that falls back to BigQuery gets a function that reads it."""
        await reconcile_one_table(
            postgres, monkeypatch, [("cpf", "BIGINT")], fallback=True
        )

        assert await function_exists(
            postgres, postgres.namespace.schema, "people_bq_fn(text, text)"
        )

    async def test_reconcile_replaces_the_columns_of_the_view_after_a_source_change(
        self, postgres: Postgres, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A new source column reaches the view without dropping it."""
        await reconcile_one_table(
            postgres, monkeypatch, [("cpf", "BIGINT"), ("name", "VARCHAR")]
        )
        await reconcile_one_table(
            postgres,
            monkeypatch,
            [("cpf", "BIGINT"), ("name", "VARCHAR"), ("active", "BOOLEAN")],
        )

        rows = await fetch_all(
            postgres,
            "postgres/view_columns",
            params={"schema_name": postgres.namespace.schema, "table_name": "people"},
        )

        assert [row[0] for row in rows] == ["cpf", "name", "active"]

    async def test_reconcile_of_one_table_leaves_the_other_tables_of_the_schema_alone(
        self, postgres: Postgres, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A source change reaches only the table that the reconcile names, and the others stay."""
        schema = postgres.namespace.schema
        config = SyncConfig(
            schemas={
                schema: SchemaConfig(
                    tables=[
                        FullTable(name=f"p.{schema}.people"),
                        FullTable(name=f"p.{schema}.pets"),
                    ]
                )
            }
        )
        columns = [("cpf", "BIGINT")]

        async def source_columns(
            _pg_conn: PgBackend, _table: TableConfig
        ) -> list[tuple[str, str]]:
            return columns

        monkeypatch.setattr(stages, "column_types_from_duckdb", source_columns)
        await initialize_schemas(postgres.backend, config)
        await reconcile_views(postgres.backend, config)
        columns = [("cpf", "BIGINT"), ("name", "VARCHAR")]

        await reconcile_views(
            postgres.backend, config, [schema], {schema: {f"p.{schema}.people"}}
        )

        names = {
            view: [
                row[0]
                for row in await fetch_all(
                    postgres,
                    "postgres/view_columns",
                    params={"schema_name": schema, "table_name": view},
                )
            ]
            for view in ("people", "pets")
        }
        assert names == {"people": ["cpf", "name"], "pets": ["cpf"]}

    async def test_reconcile_of_a_table_with_a_long_name_needs_no_reload_the_second_time(
        self, postgres: Postgres, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PostgreSQL cuts the change-feed name at 63 bytes, and the second reconcile must still find it."""
        schema = postgres.namespace.schema
        config = SyncConfig(
            schemas={
                schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.{'t' * 60}")])
            }
        )

        async def source_columns(
            _pg_conn: PgBackend, _table: TableConfig
        ) -> list[tuple[str, str]]:
            return [("cpf", "BIGINT")]

        monkeypatch.setattr(stages, "column_types_from_duckdb", source_columns)
        await initialize_schemas(postgres.backend, config)
        assert await reconcile_views(postgres.backend, config) is True

        assert await reconcile_views(postgres.backend, config) is False

    async def test_reconcile_of_an_unchanged_table_keeps_the_objects_of_its_view(
        self, postgres: Postgres, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An idle run reconciles every table, so a table that didn't change must keep its view and functions."""
        schema = postgres.namespace.schema
        config = SyncConfig(
            schemas={
                schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.people")])
            }
        )

        async def source_columns(
            _pg_conn: PgBackend, _table: TableConfig
        ) -> list[tuple[str, str]]:
            return [("cpf", "BIGINT")]

        async def object_ids() -> list[tuple[str, int]]:
            cursor = await postgres.connection.execute(
                """
                SELECT p.proname, p.oid::int
                FROM pg_proc AS p
                WHERE p.pronamespace = (SELECT oid FROM pg_namespace WHERE nspname = %(schema)s)
                UNION ALL
                SELECT c.relname, c.oid::int
                FROM pg_class AS c
                WHERE c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = %(schema)s)
                  AND c.relkind = 'v'
                ORDER BY 1
                """,
                {"schema": schema},
            )
            return [(str(name), int(oid)) for name, oid in await cursor.fetchall()]

        monkeypatch.setattr(stages, "column_types_from_duckdb", source_columns)
        await initialize_schemas(postgres.backend, config)
        first = await reconcile_views(postgres.backend, config)
        before = await object_ids()

        second = await reconcile_views(postgres.backend, config)

        assert (first, second, await object_ids()) == (True, False, before)

    async def test_reconcile_drops_the_views_and_functions_that_the_config_dropped(
        self, postgres: Postgres, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A table that leaves the config loses its view and its functions."""
        await reconcile_one_table(postgres, monkeypatch, [("cpf", "BIGINT")])
        schema = postgres.namespace.schema

        await reconcile_views(
            postgres.backend, SyncConfig(schemas={schema: SchemaConfig(tables=[])})
        )

        assert [
            await relation_exists(postgres, schema, "people"),
            await function_exists(postgres, schema, "people_fn()"),
            await function_exists(
                postgres, schema, "people_dl_fn(text, text, boolean, text[], text)"
            ),
            await function_exists(
                postgres, schema, "dl_changes_people(bigint, bigint)"
            ),
        ] == [False, False, False, False]
