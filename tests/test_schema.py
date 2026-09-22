"""Tests for schema initialization and PostgREST reload."""

import pytest

from data_proxy.models import FullTable, SchemaConfig, SyncConfig
from data_proxy.schema import initialize_schemas, revoke_anonymous_access
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql, sync_config


class TestSchema:
    """Tests for schema lifecycle behavior."""

    @pytest.mark.asyncio
    async def test_initialize_schemas_creates_roles_schemas_and_policies_in_order(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a sync config with multiple schemas.
        WHEN: initialize_schemas is called.
        THEN: roles, schemas, local policies, and policy writers are created in order.
        """
        schema = postgres.namespace.schema
        other_schema = f"{schema}_two"
        config = SyncConfig(
            schemas={
                schema: SchemaConfig(tables=[FullTable(name=f"p.{schema}.one")]),
                other_schema: SchemaConfig(
                    tables=[FullTable(name=f"p.{other_schema}.two")]
                ),
            }
        )
        await initialize_schemas(postgres.connection, config)
        query = "".join(
            [
                "SELECT to_regprocedure(name) ",
                "FROM unnest(ARRAY[",
                "'data_proxy.cleanup_stale_objects(jsonb,text)', ",
                "'data_proxy.apply_retention(jsonb,text)', ",
                "'data_proxy.prune_access_log(interval,text)'",
                "]) AS names(name)",
            ]
        )
        procedures = await (await postgres.connection.execute(query)).fetchall()
        assert procedures == [
            ("data_proxy.cleanup_stale_objects(jsonb,text)",),
            ("data_proxy.apply_retention(jsonb,text)",),
            ("data_proxy.prune_access_log(interval,text)",),
        ]
        rows = await (
            await execute_sql(
                postgres.connection,
                "postgres/schema_names",
                mapping={"schema": schema, "other_schema": other_schema},
            )
        ).fetchall()
        assert rows == [(schema,), (other_schema,)]
        await postgres.connection.execute(
            f'DROP SCHEMA "{other_schema}" CASCADE'.encode()
        )
        await postgres.connection.commit()

    @pytest.mark.asyncio
    async def test_initialize_schemas_does_not_grant_role_membership(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a sync config.
        WHEN: initialize_schemas is called.
        THEN: no role membership is granted, because CNPG owns the
              authenticator membership through inRoles.
        """
        schema = postgres.namespace.schema
        config = sync_config([FullTable(name=f"p.{schema}.one")], schema_name=schema)
        before = await (
            await execute_sql(
                postgres.connection,
                "postgres/role_memberships",
                mapping={"role": "authenticator"},
            )
        ).fetchall()
        await initialize_schemas(postgres.connection, config)
        after = await (
            await execute_sql(
                postgres.connection,
                "postgres/role_memberships",
                mapping={"role": "authenticator"},
            )
        ).fetchall()
        assert after == before

    @pytest.mark.asyncio
    async def test_revoke_anonymous_access(self, postgres: Postgres) -> None:
        """
        GIVEN: a sync config.
        WHEN: revoke_anonymous_access is called.
        THEN: anonymous access is revoked per schema.
        """
        schema = postgres.namespace.schema
        config = sync_config([FullTable(name=f"p.{schema}.one")], schema_name=schema)
        await execute_sql(
            postgres.connection,
            "postgres/setup_schema_reload_privileges",
            mapping={"schema": schema},
        )
        await postgres.connection.commit()
        await revoke_anonymous_access(postgres.connection, config)
        usage = await (
            await execute_sql(
                postgres.connection,
                "postgres/has_schema_usage",
                mapping={"schema": schema},
            )
        ).fetchone()
        assert usage == (False,)
        grant = await (
            await execute_sql(
                postgres.connection,
                "postgres/has_table_select",
                mapping={"schema": schema},
            )
        ).fetchone()
        assert grant == (False,)

    @pytest.mark.asyncio
    async def test_cleanup_stale_objects_removes_unconfigured_tables(
        self, postgres: Postgres
    ) -> None:
        schema = postgres.namespace.schema
        config = sync_config([FullTable(name=f"p.{schema}.kept")], schema_name=schema)
        await initialize_schemas(postgres.connection, config)
        await postgres.connection.execute(
            f'CREATE TABLE "{schema}".stale (id int)'.encode()
        )
        await postgres.connection.execute(
            b"CALL data_proxy.cleanup_stale_objects(%s::jsonb, %s)",
            (config.model_dump_json(), schema),
        )
        await postgres.connection.commit()
        row = await (
            await postgres.connection.execute(
                b"SELECT to_regclass(%s)", (f"{schema}.stale",)
            )
        ).fetchone()
        assert row == (None,)
