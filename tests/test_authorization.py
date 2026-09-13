from unittest.mock import AsyncMock

import pytest
from psycopg import AsyncConnection

from dp.authorization import bootstrap_table
from dp.models import UnitMapping
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
        WHEN: bootstrap_table is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            await bootstrap_table(
                AsyncMock(spec=AsyncConnection), "app", "table", invalid_rls, None
            )

    @pytest.mark.asyncio
    async def test_bootstrap_grants_access_without_rls(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a non-RLS table.
        WHEN: bootstrap_table is called.
        THEN: it receives a read grant and a schema-scope policy.
        """
        schema = postgres.namespace.schema
        await execute_sql(
            postgres.connection,
            "postgres/create_table",
            mapping={"schema": schema, "table": "table", "columns": "id_cras text"},
        )

        await bootstrap_table(
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
        WHEN: bootstrap_table is called.
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

        await bootstrap_table(
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
    async def test_rls_hides_disabled_and_ungranted_rows(
        self, postgres: Postgres
    ) -> None:
        """The user role sees only rows covered by an enabled unit grant."""
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
        await bootstrap_table(
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
        await bootstrap_table(postgres.connection, schema, "scoped", None, None)
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
        WHEN: bootstrap_table is called.
        THEN: it raises RuntimeError.
        """
        with pytest.raises(RuntimeError, match="identity claim"):
            await bootstrap_table(
                AsyncMock(spec=AsyncConnection),
                schema="app",
                table_name="table",
                rls=[UnitMapping(column="id_cras", unit_type="cras")],
                claim=None,
            )
