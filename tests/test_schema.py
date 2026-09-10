"""Tests for schema initialization and PostgREST reload."""

from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import Connection
from testcontainers.community.postgres import PostgresContainer

from dp.models import FullTable, SchemaConfig, SyncConfig
from dp.schema import initialize_schemas, reload_postgrest
from tests.conftest import PostgresTestNamespace
from tests.helpers import execute_sql, sync_config


class TestSchema:
    """Tests for schema lifecycle behavior."""

    def test_initialize_schemas_creates_roles_schemas_and_policies_in_order(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: a sync config with multiple schemas.
        WHEN: initialize_schemas is called.
        THEN: roles, schemas, local policies, and policy writers are created in order.
        """
        other_schema = f"{namespace.schema}_two"
        config = SyncConfig(
            schemas={
                namespace.schema: SchemaConfig(
                    tables=[FullTable(name=f"p.{namespace.schema}.one")]
                ),
                other_schema: SchemaConfig(
                    tables=[FullTable(name=f"p.{other_schema}.two")]
                ),
            }
        )

        initialize_schemas(postgres, config)

        assert execute_sql(
            postgres,
            "postgres/schema_names",
            mapping={"schema": namespace.schema, "other_schema": other_schema},
        ).fetchall() == [(namespace.schema,), (other_schema,)]
        postgres.execute(f'DROP SCHEMA "{other_schema}" CASCADE'.encode())
        postgres.commit()

    def test_reload_postgrest_revokes_anonymous_then_notifies(
        self,
        postgres: Connection[tuple[object, ...]],
        postgres_container: PostgresContainer,
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: a sync config.
        WHEN: reload_postgrest is called.
        THEN: anonymous access is revoked per schema before the reload notification.
        """
        config = sync_config(
            [FullTable(name=f"p.{namespace.schema}.one")], schema_name=namespace.schema
        )
        execute_sql(
            postgres,
            "postgres/setup_schema_reload_privileges",
            mapping={"schema": namespace.schema},
        )

        listener_url = urlunsplit(
            urlsplit(postgres_container.get_connection_url())._replace(
                path=f"/{postgres.info.dbname}"
            )
        )
        with psycopg.connect(listener_url, autocommit=True) as listener:
            listener.execute("LISTEN pgrst")
            reload_postgrest(postgres, config)
            postgres.commit()
            notification = next(listener.notifies(timeout=1))

        assert notification.payload == "reload schema"
        assert execute_sql(
            postgres, "postgres/has_schema_usage", mapping={"schema": namespace.schema}
        ).fetchone() == (False,)
        assert execute_sql(
            postgres, "postgres/has_table_select", mapping={"schema": namespace.schema}
        ).fetchone() == (False,)
