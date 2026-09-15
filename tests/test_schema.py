"""Tests for schema initialization and PostgREST reload."""

import psycopg
import pytest

from dp.models import FullTable, SchemaConfig, SyncConfig
from dp.schema import initialize_schemas, reload_postgrest
from tests.fixtures.types import Postgres
from tests.helpers import execute_sql, sync_config


class TestSchema:
    """Tests for schema lifecycle behavior."""

    def test_table_accepts_a_cache_lifetime(self) -> None:
        """
        GIVEN: a table entry with a cache lifetime and one without.
        WHEN: the sync config is built.
        THEN: the lifetime is kept and the other table leaves it unset.
        """
        table = FullTable(name="p.dev.eventos", cache_ttl=42)
        other = FullTable(name="p.dev.outro")

        assert table.cache_ttl == 42
        assert other.cache_ttl is None

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
    async def test_initialize_schemas_grants_anonymous_access_to_rls(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a sync config.
        WHEN: initialize_schemas is called.
        THEN: the anonymous role may use the rls schema, which holds the pre-request function.
        """
        schema = postgres.namespace.schema
        config = sync_config([FullTable(name=f"p.{schema}.one")], schema_name=schema)

        await initialize_schemas(postgres.connection, config)

        row = await (
            await execute_sql(
                postgres.connection,
                "postgres/has_schema_usage",
                mapping={"schema": "rls"},
            )
        ).fetchone()
        assert row == (True,)

    @pytest.mark.asyncio
    async def test_reload_postgrest_revokes_anonymous_then_notifies(
        self, postgres: Postgres
    ) -> None:
        """
        GIVEN: a sync config.
        WHEN: reload_postgrest is called.
        THEN: anonymous access is revoked per schema before the reload notification.
        """
        schema = postgres.namespace.schema
        config = sync_config([FullTable(name=f"p.{schema}.one")], schema_name=schema)
        await execute_sql(
            postgres.connection,
            "postgres/setup_schema_reload_privileges",
            mapping={"schema": schema},
        )
        await postgres.connection.commit()

        listener = await psycopg.AsyncConnection.connect(postgres.dsn, autocommit=True)
        try:
            await listener.execute("LISTEN pgrst")
            await reload_postgrest(postgres.connection, config)
            notification = await anext(listener.notifies(timeout=1))
        finally:
            await listener.close()

        assert notification.payload == "reload schema"
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
