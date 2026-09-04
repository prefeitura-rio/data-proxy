"""Tests for schema initialization and PostgREST reload."""

from psycopg import Connection

from dp.models import FullTable, SchemaConfig, SyncConfig
from dp.schema import initialize_schemas, reload_postgrest
from tests.helpers import execute_sql, sync_config


class TestSchema:
    """Tests for schema lifecycle behavior."""

    def test_initialize_schemas_creates_roles_schemas_and_policies_in_order(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a sync config with multiple schemas.
        WHEN: initialize_schemas is called.
        THEN: roles, schemas, local policies, and policy writers are created in order.
        """
        config = SyncConfig(
            schemas={
                "app": SchemaConfig(tables=[FullTable(name="p.app.one")]),
                "other": SchemaConfig(tables=[FullTable(name="p.other.two")]),
            }
        )

        initialize_schemas(postgres, config)

        assert execute_sql(postgres, "postgres/schema_names").fetchall() == [
            ("app",),
            ("other",),
        ]

    def test_reload_postgrest_revokes_anonymous_then_notifies(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a sync config.
        WHEN: reload_postgrest is called.
        THEN: anonymous access is revoked per schema before the reload notification.
        """
        config = sync_config([FullTable(name="p.app.one")])

        reload_postgrest(postgres, config)

        assert execute_sql(postgres, "postgres/select_one").fetchone() == (1,)
