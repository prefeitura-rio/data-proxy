from unittest.mock import AsyncMock

import pytest
from psycopg import AsyncConnection
from testcontainers.community.postgres import PostgresContainer

from data_proxy.authorization import apply_table_authorization
from data_proxy.models import FullTable, SchemaConfig, SyncConfig, UnitMapping
from data_proxy.schema import initialize_schemas
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql


class TestAuthorization:
    """Tests for authorization validation and bootstrap safety."""

    @pytest.mark.asyncio
    async def test_bootstrap_rejects_an_invalid_runtime_rls_value(
        self,
        invalid_rls: list[UnitMapping],
    ) -> None:
        """
        GIVEN: an invalid runtime RLS value.
        WHEN: apply_table_authorization is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            await apply_table_authorization(
                AsyncMock(spec=AsyncConnection), "app", "table", invalid_rls, None
            )

    @pytest.mark.asyncio
    async def test_bootstrap_grants_access_without_rls(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a non-RLS table.
        WHEN: apply_table_authorization is called.
        THEN: it receives a read grant and a schema-scope policy.
        """
        schema = postgres.namespace.schema
        await execute_sql(
            postgres.connection,
            "postgres/create_table",
            mapping={"schema": schema, "table": "table", "columns": "id_cras text"},
        )

        await apply_table_authorization(
            postgres.connection,
            schema=schema,
            table_name="table",
            rls=None,
            claim=None,
        )
        await postgres.connection.commit()

        mapping = {"schema": schema, "table": "table"}
        row = await (
            await execute_sql(
                postgres.connection, "postgres/relrowsecurity", mapping=mapping
            )
        ).fetchone()
        assert row == (True,)
        policies = await (
            await execute_sql(
                postgres.connection, "postgres/policy_names", mapping=mapping
            )
        ).fetchall()
        assert policies == [("schema_scoped",)]
        grants = await (
            await execute_sql(
                postgres.connection, "postgres/select_grants", mapping=mapping
            )
        ).fetchall()
        assert grants == [("user",)]

    @pytest.mark.asyncio
    async def test_bootstrap_installs_access_policy_check(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a protected table with RLS and an access_policy table.
        WHEN: apply_table_authorization is called.
        THEN: it renders grants and the access_policy check together.
        """
        schema = postgres.namespace.schema
        await execute_sql(
            postgres.connection,
            "postgres/create_table",
            mapping={"schema": schema, "table": "table", "columns": "id_cras text"},
        )
        await execute_sql(
            postgres.connection,
            "postgres/create_access_policy",
            mapping={"schema": schema},
        )

        await apply_table_authorization(
            postgres.connection,
            schema=schema,
            table_name="table",
            rls=[UnitMapping(column="id_cras", unit_type="cras")],
            claim="preferred_username",
        )
        await postgres.connection.commit()

        policies = await (
            await execute_sql(
                postgres.connection,
                "postgres/policy_names",
                mapping={"schema": schema, "table": "table"},
            )
        ).fetchall()
        assert policies == [("access_policy_scoped",)]

    @pytest.mark.asyncio
    async def test_rls_hides_ungranted_rows_and_revokes_on_delete(
        self, postgres: Postgres
    ) -> None:
        """The user role sees granted rows, and a deleted grant revokes access."""
        schema = postgres.namespace.schema
        await execute_sql(
            postgres.connection,
            "postgres/create_table",
            mapping={"schema": schema, "table": "visible", "columns": "id_cras text"},
        )
        await execute_sql(
            postgres.connection,
            "postgres/create_production_access_policy",
            mapping={"schema": schema},
        )
        await execute_sql(
            postgres.connection,
            "postgres/setup_unit_rls_visibility",
            mapping={"schema": schema},
        )
        await apply_table_authorization(
            postgres.connection,
            schema,
            "visible",
            [UnitMapping(column="id_cras", unit_type="cras")],
            "preferred_username",
        )
        await postgres.connection.commit()
        await postgres.connection.execute('SET ROLE "user"')
        await postgres.connection.execute(
            f"SET app.claim_schemas = '{schema}'".encode()
        )
        await postgres.connection.execute("SET app.claim_preferred_username = 'alice'")
        rows = await (
            await execute_sql(
                postgres.connection,
                "postgres/select_visible_id_cras",
                mapping={"schema": schema},
            )
        ).fetchall()
        assert rows == [("allowed",)]

        await postgres.connection.execute("RESET ROLE")
        await postgres.connection.execute(
            f"DELETE FROM {schema}.access_policy WHERE subject = 'alice'".encode()
        )
        await postgres.connection.execute('SET ROLE "user"')
        rows = await (
            await execute_sql(
                postgres.connection,
                "postgres/select_visible_id_cras",
                mapping={"schema": schema},
            )
        ).fetchall()
        assert rows == []

    @pytest.mark.asyncio
    async def test_schema_scope_rls_hides_rows_outside_claimed_schema(
        self, postgres: Postgres
    ) -> None:
        """A schema-scoped table is visible only when the schema claim matches."""
        schema = postgres.namespace.schema
        await execute_sql(
            postgres.connection,
            "postgres/create_table",
            mapping={"schema": schema, "table": "scoped", "columns": "id text"},
        )
        await execute_sql(
            postgres.connection,
            "postgres/insert_scoped_row",
            mapping={"schema": schema},
        )
        await postgres.connection.commit()
        await apply_table_authorization(
            postgres.connection, schema, "scoped", None, None
        )
        await postgres.connection.commit()
        await execute_sql(
            postgres.connection,
            "postgres/grant_user_schema_usage",
            mapping={"schema": schema},
        )
        await postgres.connection.commit()
        await postgres.connection.execute('SET ROLE "user"')
        await postgres.connection.execute(
            f"SET app.claim_schemas = '{schema}'".encode()
        )
        rows = await (
            await execute_sql(
                postgres.connection,
                "postgres/select_scoped_ids",
                mapping={"schema": schema},
            )
        ).fetchall()
        assert rows == [("visible",)]
        await postgres.connection.execute("SET app.claim_schemas = 'other'")
        rows = await (
            await execute_sql(
                postgres.connection,
                "postgres/select_scoped_ids",
                mapping={"schema": schema},
            )
        ).fetchall()
        assert rows == []

    @pytest.mark.asyncio
    async def test_bootstrap_requires_a_configured_claim_for_protected_tables(
        self,
    ) -> None:
        """
        GIVEN: a protected table without a configured schema claim.
        WHEN: apply_table_authorization is called.
        THEN: it raises RuntimeError.
        """
        with pytest.raises(RuntimeError, match="identity claim"):
            await apply_table_authorization(
                AsyncMock(spec=AsyncConnection),
                schema="app",
                table_name="table",
                rls=[UnitMapping(column="id_cras", unit_type="cras")],
                claim=None,
            )


@pytest.fixture
async def access_policy(postgres: Postgres) -> str:
    """Create the production access_policy and access_log objects in one schema."""
    schema = postgres.namespace.schema
    config = SyncConfig(
        schemas={schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.table")])}
    )
    await initialize_schemas(postgres.connection, config)
    return schema


class TestAccessPolicyLog:
    """Tests for the access_log audit trail trigger."""

    @pytest.mark.asyncio
    async def test_log_trigger_runs_as_definer(
        self, postgres: Postgres, access_policy: str
    ) -> None:
        """
        GIVEN: the production template has been applied.
        WHEN: the log trigger function is inspected.
        THEN: it is SECURITY DEFINER, so low-privilege writers can be logged.
        """
        row = await (
            await execute_sql(
                postgres.connection,
                "postgres/log_trigger_is_security_definer",
                mapping={"schema": access_policy},
            )
        ).fetchone()
        assert row == (True,)

    @pytest.mark.asyncio
    async def test_insert_logs_to_access_log(
        self, postgres: Postgres, access_policy: str
    ) -> None:
        """
        GIVEN: an access_policy table with the log trigger.
        WHEN: a row is inserted.
        THEN: an 'insert' log entry is created with the new row state.
        """
        schema = access_policy
        await postgres.connection.execute(
            (
                f"INSERT INTO {schema}.access_policy (subject, is_admin, unit_type, unit_id) "
                f"VALUES ('123', true, 'cras', '42')"
            ).encode()
        )
        await postgres.connection.commit()

        rows = await (
            await execute_sql(
                postgres.connection,
                "postgres/access_log_entries",
                mapping={"schema": schema},
            )
        ).fetchall()
        assert rows == [("123", True, "cras", "42", "insert")]

    @pytest.mark.asyncio
    async def test_update_logs_old_state_to_access_log(
        self, postgres: Postgres, access_policy: str
    ) -> None:
        """
        GIVEN: an access_policy table with the log trigger and one grant.
        WHEN: the grant is updated.
        THEN: an 'update' log entry captures the previous state.
        """
        schema = access_policy
        await postgres.connection.execute(
            (
                f"INSERT INTO {schema}.access_policy (subject, is_admin, unit_type, unit_id) "
                f"VALUES ('456', false, 'escola', '7')"
            ).encode()
        )
        await postgres.connection.commit()

        await postgres.connection.execute(
            (
                f"UPDATE {schema}.access_policy SET is_admin = true WHERE subject = '456'"
            ).encode()
        )
        await postgres.connection.commit()

        rows = await (
            await execute_sql(
                postgres.connection,
                "postgres/access_log_entries",
                mapping={"schema": schema},
            )
        ).fetchall()
        assert len(rows) == 2
        assert rows[0] == ("456", False, "escola", "7", "insert")
        assert rows[1] == ("456", False, "escola", "7", "update")

    @pytest.mark.asyncio
    async def test_delete_logs_old_state_to_access_log(
        self, postgres: Postgres, access_policy: str
    ) -> None:
        """
        GIVEN: an access_policy table with the log trigger and one grant.
        WHEN: the grant is deleted.
        THEN: a 'delete' log entry captures the removed state.
        """
        schema = access_policy
        await postgres.connection.execute(
            (
                f"INSERT INTO {schema}.access_policy (subject, is_admin, unit_type, unit_id) "
                f"VALUES ('789', false, 'ap', '1')"
            ).encode()
        )
        await postgres.connection.commit()

        await postgres.connection.execute(
            f"DELETE FROM {schema}.access_policy WHERE subject = '789'".encode()
        )
        await postgres.connection.commit()

        rows = await (
            await execute_sql(
                postgres.connection,
                "postgres/access_log_entries",
                mapping={"schema": schema},
            )
        ).fetchall()
        assert len(rows) == 2
        assert rows[0] == ("789", False, "ap", "1", "insert")
        assert rows[1] == ("789", False, "ap", "1", "delete")

    @pytest.mark.asyncio
    async def test_cleanup_prunes_access_log_after_retention(
        self, postgres: Postgres, access_policy: str
    ) -> None:
        """
        GIVEN: an access_log entry older than the retention period.
        WHEN: the access log cleanup SQL runs.
        THEN: only entries inside the retention window remain.
        """
        schema = access_policy
        await postgres.connection.execute(
            (
                f"INSERT INTO {schema}.access_policy (subject, is_admin, unit_type, unit_id) "
                f"VALUES ('recent', true, 'cras', '1')"
            ).encode()
        )
        await postgres.connection.execute(
            (
                f"INSERT INTO {schema}.access_log "
                f"(subject, is_admin, unit_type, unit_id, action, changed_at) "
                f"VALUES ('stale', false, 'escola', '2', 'delete', now() - interval '100 days')"
            ).encode()
        )
        await postgres.connection.commit()

        await postgres.connection.execute(
            b"CALL data_proxy.prune_access_log(%s::interval, %s)",
            ("90 days", schema),
        )
        await postgres.connection.commit()

        rows = await (
            await execute_sql(
                postgres.connection,
                "postgres/access_log_entries",
                mapping={"schema": schema},
            )
        ).fetchall()
        assert [row[0] for row in rows] == ["recent"]


class TestAccessPolicyBackup:
    """Tests for dumping the governance tables the way the backup routine does."""

    @pytest.mark.asyncio
    async def test_backup_dumps_policy_and_log(
        self,
        postgres: Postgres,
        postgres_container: PostgresContainer,
        access_policy: str,
    ) -> None:
        """
        GIVEN: an access_policy row and its access_log entry.
        WHEN: pg_dump exports both tables the way the backup routine does.
        THEN: both dumps succeed and contain the exported row.
        """
        schema = access_policy
        await postgres.connection.execute(
            (
                f"INSERT INTO {schema}.access_policy (subject, is_admin, unit_type, unit_id) "
                f"VALUES ('dump', true, 'cras', '1')"
            ).encode()
        )
        await postgres.connection.commit()

        for table in ("access_policy", "access_log"):
            result = postgres_container.exec(
                [
                    "pg_dump",
                    f"--username={postgres_container.username}",
                    "--dbname=test_template",
                    "--format=plain",
                    "--no-owner",
                    "--no-acl",
                    "--data-only",
                    f"--table={schema}.{table}",
                ]
            )
            assert result.exit_code == 0, result.output
            assert "dump" in result.output.decode()
