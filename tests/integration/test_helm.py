"""Integration tests for the SQL that the Helm jobs install and run."""

import json
from time import time
from typing import Final

import psycopg
import pytest
from psycopg.sql import SQL, Identifier, Literal

from data_proxy.conditions import schema_scope_condition
from data_proxy.settings import settings
from data_proxy.templates import render_template
from tests.constants import TEST_SQL_DIR
from tests.fixtures.types import Postgres, Psql
from tests.helpers import (
    execute_sql,
    fetch_all,
    function_exists,
    helm_sql,
    insert_access_policy,
    psql_script,
    relation_exists,
    scalar,
    set_setting,
)

pytestmark = pytest.mark.postgres

HOUR_MS: Final = 3_600_000


class TestPreRequest:
    """data_proxy.pre_request maps JWT claims into session settings."""

    @pytest.mark.parametrize(
        ("claims", "expected"),
        [
            pytest.param(
                {"sub": "u1", "schemas": ["a", "b"]},
                {"app.claim_sub": "u1", "app.claim_schemas": "a,b"},
                id="string-and-array-claims",
            ),
            pytest.param(
                {"preferred_username": "test_user_1", "level": 5},
                {"app.claim_preferred_username": "test_user_1", "app.claim_level": "5"},
                id="string-and-number-claims",
            ),
            pytest.param(None, {"app.claim_sub": None}, id="no-claims"),
        ],
    )
    async def test_maps_each_claim_to_an_app_setting(
        self,
        postgres: Postgres,
        claims: dict[str, str | int | list[str]] | None,
        expected: dict[str, str | None],
    ) -> None:
        await postgres.connection.execute(
            helm_sql(
                "create_pre_request",
                {"schema": Identifier(settings.DBOS_APP_SCHEMA)},
            ).encode()
        )
        if claims is not None:
            await set_setting(postgres, "request.jwt.claims", json.dumps(claims))

        await postgres.connection.execute("SELECT data_proxy.pre_request()")

        assert {
            name: await scalar(postgres, "SELECT current_setting(%s, true)", name)
            for name in expected
        } == expected


class TestGrantAppUsage:
    """grant_app_usage lets request roles call the pre-request function."""

    async def test_grants_usage_and_execute_on_data_proxy(
        self, postgres: Postgres, policy_writer: str
    ) -> None:
        roles = [settings.AUTH_ANON_ROLE, settings.AUTH_USER_ROLE, policy_writer]
        await postgres.connection.execute(
            SQL("REVOKE USAGE ON SCHEMA data_proxy FROM {}").format(
                SQL(", ").join(Identifier(role) for role in roles)
            )
        )
        await postgres.connection.execute(
            SQL("REVOKE EXECUTE ON FUNCTION data_proxy.pre_request() FROM {}").format(
                SQL(", ").join(Identifier(role) for role in roles)
            )
        )

        await postgres.connection.execute(
            helm_sql(
                "grant_app_usage",
                {
                    "app_schema": Identifier(settings.DBOS_APP_SCHEMA),
                    "anonymous_role": Identifier(settings.AUTH_ANON_ROLE),
                    "user_role": Identifier(settings.AUTH_USER_ROLE),
                    "policy_writer_roles": [Identifier(policy_writer)],
                },
            ).encode()
        )

        assert [
            await scalar(
                postgres, "SELECT has_schema_privilege(%s, 'data_proxy', 'USAGE')", role
            )
            for role in roles
        ] == [True, True]
        assert [
            await scalar(
                postgres,
                "SELECT has_function_privilege(%s, 'data_proxy.pre_request()', 'EXECUTE')",
                role,
            )
            for role in roles
        ] == [True, True]


class TestRenderDucklakeWhere:
    """The proxy-pushed filter is validated and rendered into a WHERE predicate."""

    async def _install(self, postgres: Postgres) -> None:
        await postgres.connection.execute(
            render_template(
                "postgres/views/routing/filter",
                {"schema": Identifier(settings.DBOS_APP_SCHEMA)},
            )
        )

    async def test_renders_where_nodes(self, postgres: Postgres) -> None:
        await self._install(postgres)

        cases = [
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "gte",
                        "negated": False,
                        "value": "10",
                    }
                },
                "(\"id\" >= '10')",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "in",
                        "negated": False,
                        "value": ["1", "2"],
                    }
                },
                "(\"id\" IN ('1', '2'))",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "name",
                        "operator": "ilike",
                        "negated": False,
                        "value": "*rio*",
                    }
                },
                "(\"name\" ILIKE '%rio%')",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "eq",
                        "negated": True,
                        "value": "1",
                    }
                },
                "NOT ((\"id\" = '1'))",
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "is",
                        "negated": False,
                        "value": "not_null",
                    }
                },
                '("id" IS NOT NULL)',
            ),
            (
                {
                    "where": {
                        "kind": "comparison",
                        "column": "id",
                        "operator": "is",
                        "negated": False,
                        "value": "unknown",
                    }
                },
                '("id" IS UNKNOWN)',
            ),
            (
                {
                    "where": {
                        "kind": "group",
                        "operator": "or",
                        "items": [
                            {
                                "kind": "comparison",
                                "column": "id",
                                "operator": "eq",
                                "negated": False,
                                "value": "1",
                            },
                            {
                                "kind": "comparison",
                                "column": "name",
                                "operator": "eq",
                                "negated": False,
                                "value": "x",
                            },
                        ],
                    }
                },
                "((\"id\" = '1') OR (\"name\" = 'x'))",
            ),
        ]

        for payload, expected in cases:
            rendered = await scalar(
                postgres,
                "SELECT data_proxy.render_dl_where(%s, ARRAY['id', 'name']::text[])",
                json.dumps(payload),
            )
            assert rendered == expected

    async def test_rejects_unknown_columns_and_operators(
        self, postgres: Postgres
    ) -> None:
        await self._install(postgres)

        unknown_column = {
            "where": {
                "kind": "comparison",
                "column": "nope",
                "operator": "eq",
                "negated": False,
                "value": "1",
            }
        }
        with pytest.raises(psycopg.Error) as error:
            await scalar(
                postgres,
                "SELECT data_proxy.render_dl_where(%s, ARRAY['id']::text[])",
                json.dumps(unknown_column),
            )
        assert error.value.sqlstate == "PT400"

        unknown_operator = {
            "where": {
                "kind": "comparison",
                "column": "id",
                "operator": "fts",
                "negated": False,
                "value": "x",
            }
        }
        with pytest.raises(psycopg.Error) as error:
            await scalar(
                postgres,
                "SELECT data_proxy.render_dl_where(%s, ARRAY['id']::text[])",
                json.dumps(unknown_operator),
            )
        assert error.value.sqlstate == "PT400"

    async def test_request_filter_reads_the_header(self, postgres: Postgres) -> None:
        await self._install(postgres)

        assert await scalar(postgres, "SELECT data_proxy.request_filter()") is None

        payload = {
            "where": {
                "kind": "comparison",
                "column": "id",
                "operator": "eq",
                "negated": False,
                "value": "1",
            }
        }
        await set_setting(
            postgres,
            "request.headers",
            json.dumps({"x-ducklake-filter": json.dumps(payload)}),
        )
        assert await scalar(postgres, "SELECT data_proxy.request_filter()") == payload

        await set_setting(
            postgres, "request.headers", json.dumps({"x-ducklake-filter": "not-json"})
        )
        with pytest.raises(psycopg.Error) as error:
            await postgres.connection.execute("SELECT data_proxy.request_filter()")
        assert error.value.sqlstate == "PT400"


class TestPolicyWriter:
    """The policy writer manages grants in its own schema only."""

    async def test_writes_grants_in_its_schema_and_logs_them(
        self, postgres: Postgres, policy_writer: str
    ) -> None:
        await postgres.connection.execute(
            SQL("SET ROLE {}").format(Identifier(policy_writer))
        )

        await insert_access_policy(postgres, "test_user_1", "unit", "unit_1")
        await postgres.connection.execute("RESET ROLE")

        assert await fetch_all(
            postgres,
            "postgres/access_log_entries",
            mapping={"schema": postgres.namespace.identifier},
        ) == [("test_user_1", "unit", "unit_1", "insert")]

    async def test_leaves_the_authenticator_membership_to_cnpg(
        self, postgres: Postgres, policy_writer: str
    ) -> None:
        memberships = await fetch_all(
            postgres,
            "postgres/role_memberships",
            params={"role_name": settings.AUTH_AUTHENTICATOR_ROLE},
        )

        assert (policy_writer,) not in memberships

    async def test_cannot_write_grants_in_another_schema(
        self, postgres: Postgres, policy_writer: str
    ) -> None:
        await postgres.connection.execute(
            SQL("SET ROLE {}").format(Identifier(policy_writer))
        )

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await execute_sql(
                postgres,
                "postgres/insert_access_policy",
                mapping={"schema": Identifier(f"{postgres.namespace.schema}_other")},
                params={
                    "subject": "test_user_1",
                    "unit_type": "unit",
                    "unit_id": "unit_1",
                },
            )

    @pytest.mark.parametrize(
        ("claimed", "visible"),
        [
            pytest.param("own", [("test_user_1",)], id="schema-claimed"),
            pytest.param("other", [], id="schema-not-claimed"),
        ],
    )
    async def test_user_reads_grants_only_for_a_claimed_schema(
        self,
        postgres: Postgres,
        policy_writer: str,
        claimed: str,
        visible: list[tuple[str]],
    ) -> None:
        schema = postgres.namespace.schema
        await insert_access_policy(postgres, "test_user_1", "unit", "unit_1")
        await set_setting(
            postgres,
            "app.claim_schemas",
            schema if claimed == "own" else f"{schema}_other",
        )
        await postgres.connection.execute(
            SQL("SET ROLE {}").format(Identifier(settings.AUTH_USER_ROLE))
        )

        assert (
            await fetch_all(
                postgres,
                "postgres/select_access_policy_subjects",
                mapping={"schema": postgres.namespace.identifier},
            )
            == visible
        )


class TestCleanupStaleObjects:
    """The maintenance job drops views that left the sync config."""

    async def test_drops_only_views_missing_from_the_config(
        self, postgres: Postgres, psql: Psql
    ) -> None:
        schema = postgres.namespace.schema
        target = postgres.namespace.identifier
        psql.run(
            psql_script(
                render_template("postgres/cleanup_stale_objects", {"schema": target}),
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
        ] == [True, False, False]


class TestPruneAccessLog:
    """The backup job deletes access-log rows older than the retention window."""

    async def test_keeps_only_recent_rows(self, postgres: Postgres, psql: Psql) -> None:
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
                render_template("postgres/prune_access_log", {"schema": target}),
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


class TestRecoverOrphanedWorkflows:
    """Recovery re-enqueues queued workflows whose executor pod is gone."""

    async def test_re_enqueues_only_orphaned_workflows(
        self, postgres: Postgres, psql: Psql, dbos_schema: str
    ) -> None:
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
