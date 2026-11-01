"""Tests for the pre-request, access policy and trigger templates."""

import itertools
import json
from asyncio import timeout
from time import perf_counter
from typing import LiteralString

import pytest
from psycopg import AsyncConnection
from psycopg.sql import SQL, Identifier

from sync.postgres import Postgres as PgBackend
from sync.schema import notify_postgrest, wait_for_standbys
from sync.settings import settings
from tests.fixtures.types import Postgres
from tests.helpers.core import (
    fetch_all,
    helm_sql,
    scalar,
    set_request_headers,
    set_setting,
)
from tests.helpers.rbac import install_console_triggers, queued_workflows, revision
from tests.helpers.routing import (
    act_as_token,
    create_access_policy,
    insert_access_policy,
)


@pytest.mark.postgres
class TestPreRequest:
    """The pre_request function that maps the claims of a token to session settings."""

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
            pytest.param(
                {"sub": "u1", "allowed-origins": ["http://console.localhost"]},
                {"app.claim_sub": "u1"},
                id="claim-whose-name-is-not-a-setting-name",
            ),
        ],
    )
    async def test_pre_request_maps_each_claim_to_an_app_setting(
        self,
        rbac: Postgres,
        claims: dict[str, str | int | list[str]] | None,
        expected: dict[str, str | None],
    ) -> None:
        """Copy every claim of the token into a setting of the transaction."""
        postgres = rbac
        await postgres.connection.execute(
            helm_sql(
                "create_pre_request", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
            ).encode()
        )
        if claims is not None:
            await set_setting(postgres, "request.jwt.claims", json.dumps(claims))

        await postgres.connection.execute("SELECT data_proxy.pre_request()")

        assert {
            name: await scalar(postgres, "SELECT current_setting(%s, true)", name)
            for name in expected
        } == expected

    @pytest.mark.parametrize(
        ("claims", "can_read_sync", "can_start_sync", "mine_pic"),
        [
            pytest.param(
                {"role": "dp_console", "schemas": ["pic"]},
                True,
                False,
                True,
                id="console-of-one-schema",
            ),
            pytest.param(
                {"role": "dp_admin", "schemas": ["pic", "brt"]},
                True,
                True,
                True,
                id="admin-of-two-schemas",
            ),
            pytest.param(
                {"role": "dp_admin", "schemas": ["brt"]},
                True,
                True,
                False,
                id="admin-of-another-schema",
            ),
            pytest.param(
                {"role": "dp_ghost", "schemas": ["pic"]},
                False,
                False,
                True,
                id="unknown-role",
            ),
            pytest.param(None, False, False, False, id="no-token"),
        ],
    )
    async def test_pre_request_loads_the_rules_for_the_role_and_schemas_of_the_token(
        self,
        rbac: Postgres,
        claims: dict[str, str | list[str]] | None,
        can_read_sync: bool,
        can_start_sync: bool,
        mine_pic: bool,
    ) -> None:
        """After the pre-request function, the checks answer for the role and the schemas of the token."""
        await rbac.connection.execute(
            helm_sql(
                "create_pre_request", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
            ).encode()
        )
        if claims is not None:
            await set_setting(rbac, "request.jwt.claims", json.dumps(claims))

        await rbac.connection.execute("SELECT data_proxy.pre_request()")

        assert [
            await scalar(rbac, "SELECT rbac.can('read_sync')"),
            await scalar(rbac, "SELECT rbac.can('start_sync')"),
            await scalar(rbac, "SELECT rbac.mine('pic')"),
        ] == [can_read_sync, can_start_sync, mine_pic]

    @pytest.mark.parametrize(
        ("role", "can_control_sync"),
        [
            pytest.param("dp_admin", True, id="admin"),
            pytest.param("dp_console", False, id="console"),
            pytest.param("dp_policy", False, id="policy"),
            pytest.param("dp_api", False, id="api"),
        ],
    )
    async def test_only_the_admin_controls_runs_and_schedules(
        self, rbac: Postgres, role: str, can_control_sync: bool
    ) -> None:
        """Cancelling a run and pausing a schedule are rights of dp_admin only."""
        await rbac.connection.execute(
            helm_sql(
                "create_pre_request", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
            ).encode()
        )
        await set_setting(
            rbac,
            "request.jwt.claims",
            json.dumps({"role": role, "schemas": ["pic"]}),
        )

        await rbac.connection.execute("SELECT data_proxy.pre_request()")

        assert await scalar(rbac, "SELECT rbac.can('control_sync')") is can_control_sync

    @pytest.mark.parametrize(
        ("profile", "longer"),
        [
            pytest.param("console", True, id="console-profile"),
            pytest.param("sales", False, id="data-profile"),
        ],
    )
    async def test_pre_request_gives_the_console_profile_a_longer_timeout(
        self, rbac: Postgres, profile: str, longer: bool
    ) -> None:
        """A request to the console reads views over the engine tables, so it may run for 30 seconds."""
        postgres = rbac
        await postgres.connection.execute(
            helm_sql(
                "create_pre_request", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
            ).encode()
        )
        before = await scalar(postgres, "SELECT current_setting('statement_timeout')")
        await set_request_headers(postgres, {"accept-profile": profile})

        await postgres.connection.execute("SELECT data_proxy.pre_request()")

        after = await scalar(postgres, "SELECT current_setting('statement_timeout')")
        assert after == ("30s" if longer else before)


@pytest.mark.postgres
class TestAccessPolicyTemplates:
    """The access policy, its triggers, the reload and the console triggers."""

    async def test_create_reload_notifies_postgrest_and_does_not_wait_without_a_standby(
        self, postgres: Postgres
    ) -> None:
        """A listener on the pgrst channel gets the message at once when no standby streams."""
        async with (
            await AsyncConnection.connect(postgres.dsn, autocommit=True) as writer,
            await AsyncConnection.connect(postgres.dsn, autocommit=True) as listener,
        ):
            await writer.execute(
                helm_sql(
                    "create_reload", {"schema": Identifier(settings.DBOS_APP_SCHEMA)}
                ).encode()
            )
            await listener.execute(b"LISTEN pgrst")
            started = perf_counter()

            await notify_postgrest(PgBackend(connection=writer))
            await wait_for_standbys(PgBackend(connection=writer))

            async with timeout(2):
                async for notification in listener.notifies():
                    assert notification.payload == "reload schema"
                    break
            assert perf_counter() - started < 1

    async def test_setup_access_policy_logs_every_change(
        self, postgres: Postgres
    ) -> None:
        """The trigger writes the old row of an update and a delete, and the new row of an insert."""
        await create_access_policy(postgres)
        table = postgres.namespace.identifier

        await insert_access_policy(postgres, "ana", "unit", "u1")
        await postgres.connection.execute(
            SQL("UPDATE {}.access_policy SET unit_id = 'u2'").format(table)
        )
        await postgres.connection.execute(
            SQL("DELETE FROM {}.access_policy").format(table)
        )

        assert sorted(
            await fetch_all(
                postgres, "postgres/access_log_entries", mapping={"schema": table}
            )
        ) == [
            ("ana", "unit", "u1", "insert"),
            ("ana", "unit", "u1", "update"),
            ("ana", "unit", "u2", "delete"),
        ]

    @pytest.mark.parametrize(
        ("role", "claim", "expected"),
        [
            pytest.param("dp_policy", "{schema}", 1, id="policy-role-of-the-schema"),
            pytest.param("dp_policy", "other", 0, id="policy-role-of-another-schema"),
            pytest.param("dp_api", "{schema}", 0, id="role-without-read-policy"),
        ],
    )
    async def test_setup_access_policy_shows_the_log_to_a_token_that_may_read_the_policy(
        self, rbac: Postgres, role: str, claim: str, expected: int
    ) -> None:
        """A token reads the access log of a schema when it holds read_policy for that schema."""
        await create_access_policy(rbac)
        await insert_access_policy(rbac, "ana", "unit", "u1")
        await act_as_token(rbac, role, claim.format(schema=rbac.namespace.schema))
        token_role = Identifier(settings.AUTH_USER_ROLE)
        await rbac.connection.execute(
            SQL(
                "GRANT USAGE ON SCHEMA rbac TO {role}; GRANT EXECUTE ON FUNCTION rbac.can(text), rbac.mine(text) TO {role}; SET LOCAL ROLE {role}"
            ).format(role=token_role)
        )

        assert (
            await scalar(
                rbac,
                SQL("SELECT count(*) FROM {}.access_log")
                .format(rbac.namespace.identifier)
                .as_string(),
            )
            == expected
        )

    @pytest.mark.parametrize(
        ("statement", "expected"),
        [
            pytest.param(
                "INSERT INTO console.schemas VALUES ('a')",
                ["provision_schemas", "reconcile_schedules", "reload_proxy"],
                id="schema-added",
            ),
            pytest.param(
                "UPDATE console.schemas SET name = name",
                ["provision_schemas", "reload_proxy"],
                id="schema-changed",
            ),
            pytest.param(
                "DELETE FROM console.schemas",
                ["provision_schemas", "reconcile_schedules", "reload_proxy"],
                id="schema-removed",
            ),
            pytest.param(
                "INSERT INTO console.tables VALUES ('t')",
                ["reload_proxy"],
                id="table-added",
            ),
            pytest.param(
                "DELETE FROM console.tables",
                ["cleanup_tables", "reload_proxy"],
                id="table-removed",
            ),
            pytest.param(
                "UPDATE rbac.revision SET value = value + 1",
                ["reload_proxy"],
                id="rules-or-policy-revision-moved",
            ),
            pytest.param(
                "INSERT INTO {policy} VALUES ('ana', 'unit', 'u1') ON CONFLICT DO NOTHING",
                ["reload_proxy"],
                id="policy-written",
            ),
        ],
    )
    async def test_create_console_triggers_queues_the_workflow_that_the_change_needs(
        self,
        rbac: Postgres,
        dbos_schema: str,
        statement: LiteralString,
        expected: list[str],
    ) -> None:
        """A change queues each workflow once, so that a burst of changes shares one run."""
        await create_access_policy(rbac)
        await install_console_triggers(rbac)
        sql = SQL(statement).format(
            policy=SQL("{}.access_policy").format(rbac.namespace.identifier)
        )

        before = await queued_workflows(rbac, dbos_schema)
        await rbac.connection.execute(sql)
        await rbac.connection.execute(sql)

        queued = await queued_workflows(rbac, dbos_schema)
        assert sorted(set(queued) - set(before)) == expected
        assert len(queued) - len(before) == len(expected)

    async def test_create_console_triggers_queues_the_reconcile_of_the_schedules_in_a_queue_of_its_own(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """The queue runs one reconcile at a time, so two changes never create the same schedule twice."""
        await install_console_triggers(rbac)

        await rbac.connection.execute(SQL("INSERT INTO console.schemas VALUES ('a')"))

        cursor = await rbac.connection.execute(
            SQL(
                "SELECT queue_name FROM {}.workflow_status WHERE name = 'reconcile_schedules'"
            ).format(Identifier(dbos_schema))
        )
        assert [queue for (queue,) in await cursor.fetchall()] == ["schedules"]

    async def test_create_console_triggers_queues_the_next_run_while_one_is_running(
        self, rbac: Postgres, dbos_schema: str
    ) -> None:
        """A change that arrives during a run waits for the next run, because the running one may have read the data already."""
        await install_console_triggers(rbac)
        insert = SQL("INSERT INTO console.schemas VALUES ('a')")
        await rbac.connection.execute(insert)
        await rbac.connection.execute(
            SQL(
                "UPDATE {}.workflow_status SET status = 'PENDING' WHERE name = 'provision_schemas' AND status = 'ENQUEUED'"
            ).format(Identifier(dbos_schema))
        )

        await rbac.connection.execute(insert)
        await rbac.connection.execute(insert)

        cursor = await rbac.connection.execute(
            SQL(
                "SELECT status FROM {}.workflow_status WHERE name = 'provision_schemas' ORDER BY status"
            ).format(Identifier(dbos_schema))
        )
        assert [status for (status,) in await cursor.fetchall()] == [
            "ENQUEUED",
            "PENDING",
        ]

    async def test_setup_access_policy_raises_the_revision_once_for_each_write_statement(
        self, rbac: Postgres
    ) -> None:
        """The proxy learns of a policy change from the revision, so each write statement moves it by one."""
        await create_access_policy(rbac)
        table = rbac.namespace.identifier
        revisions = [await revision(rbac)]

        for statement in (
            "INSERT INTO {} VALUES ('ana', 'unit', 'u1'), ('bia', 'unit', 'u2')",
            "UPDATE {} SET unit_id = 'u3'",
            "DELETE FROM {}",
        ):
            await rbac.connection.execute(
                SQL(statement).format(SQL("{}.access_policy").format(table))
            )
            revisions.append(await revision(rbac))

        assert [after - before for before, after in itertools.pairwise(revisions)] == [
            1,
            1,
            1,
        ]

    async def test_setup_access_policy_makes_a_policy_write_wait_for_the_standbys(
        self, postgres: Postgres
    ) -> None:
        """A policy write raises synchronous_commit for its own transaction only."""
        await create_access_policy(postgres)

        before = await scalar(postgres, "SELECT current_setting('synchronous_commit')")
        await insert_access_policy(postgres, "ana", "unit", "u1")
        during = await scalar(postgres, "SELECT current_setting('synchronous_commit')")
        await postgres.connection.rollback()
        after = await scalar(postgres, "SELECT current_setting('synchronous_commit')")

        assert (before, during, after) == ("on", "remote_apply", "on")
