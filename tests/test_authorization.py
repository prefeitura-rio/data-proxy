from unittest.mock import MagicMock

import pytest
from psycopg import Connection

from dp.authorization import (
    bootstrap_table,
    claim_session_var,
    schema_scope_predicate,
)
from dp.models import UnitMapping
from tests.conftest import PostgresTestNamespace
from tests.helpers import execute_sql


class TestAuthorization:
    """Tests for authorization validation and bootstrap safety."""

    def test_bootstrap_rejects_an_invalid_runtime_rls_value(
        self,
        invalid_rls: list[UnitMapping],
    ) -> None:
        """
        GIVEN: an invalid runtime RLS value.
        WHEN: bootstrap_table is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            bootstrap_table(
                MagicMock(spec=Connection), "app", "table", invalid_rls, None
            )

    def test_bootstrap_grants_access_without_rls(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: a non-RLS table.
        WHEN: bootstrap_table is called.
        THEN: it receives a read grant and a schema-scope policy.
        """
        schema = namespace.schema
        execute_sql(
            postgres,
            "postgres/create_table",
            mapping={
                "schema": schema,
                "table": "table",
                "columns": "id_cras text",
            },
        )

        bootstrap_table(
            postgres,
            schema=schema,
            table_name="table",
            rls=None,
            claim=None,
        )

        mapping = {"schema": schema, "table": "table"}
        assert execute_sql(
            postgres, "postgres/relrowsecurity", mapping=mapping
        ).fetchone() == (True,)
        assert execute_sql(
            postgres, "postgres/policy_names", mapping=mapping
        ).fetchall() == [("schema_scoped",)]
        assert execute_sql(
            postgres, "postgres/select_grants", mapping=mapping
        ).fetchall() == [("user",)]

    def test_bootstrap_installs_access_policy_check(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """
        GIVEN: a protected table with RLS and an access_policy table.
        WHEN: bootstrap_table is called.
        THEN: it renders grants and the access_policy check together.
        """
        schema = namespace.schema
        execute_sql(
            postgres,
            "postgres/create_table",
            mapping={
                "schema": schema,
                "table": "table",
                "columns": "id_cras text",
            },
        )
        execute_sql(
            postgres, "postgres/create_access_policy", mapping={"schema": schema}
        )

        bootstrap_table(
            postgres,
            schema=schema,
            table_name="table",
            rls=[UnitMapping(column="id_cras", unit_type="cras")],
            claim="preferred_username",
        )

        assert execute_sql(
            postgres,
            "postgres/policy_names",
            mapping={"schema": schema, "table": "table"},
        ).fetchall() == [("access_policy_scoped",)]

    def test_rls_hides_disabled_and_ungranted_rows(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """The user role sees only rows covered by an enabled unit grant."""
        schema = namespace.schema
        execute_sql(
            postgres,
            "postgres/create_table",
            mapping={"schema": schema, "table": "visible", "columns": "id_cras text"},
        )
        execute_sql(
            postgres,
            "postgres/create_production_access_policy",
            mapping={"schema": schema},
        )
        execute_sql(
            postgres, "postgres/setup_unit_rls_visibility", mapping={"schema": schema}
        )
        bootstrap_table(
            postgres,
            schema,
            "visible",
            [UnitMapping(column="id_cras", unit_type="cras")],
            "preferred_username",
        )
        postgres.commit()
        postgres.execute('SET ROLE "user"')
        postgres.execute(f"SET app.claim_schemas = '{schema}'".encode())
        postgres.execute("SET app.claim_preferred_username = 'alice'")
        assert execute_sql(
            postgres, "postgres/select_visible_id_cras", mapping={"schema": schema}
        ).fetchall() == [("allowed",)]

    def test_schema_scope_rls_hides_rows_outside_claimed_schema(
        self,
        postgres: Connection[tuple[object, ...]],
        namespace: PostgresTestNamespace,
    ) -> None:
        """A schema-scoped table is visible only when the schema claim matches."""
        execute_sql(
            postgres,
            "postgres/create_table",
            mapping={
                "schema": namespace.schema,
                "table": "scoped",
                "columns": "id text",
            },
        )
        schema = namespace.schema
        execute_sql(postgres, "postgres/insert_scoped_row", mapping={"schema": schema})
        bootstrap_table(postgres, schema, "scoped", None, None)
        execute_sql(
            postgres, "postgres/grant_user_schema_usage", mapping={"schema": schema}
        )
        postgres.commit()
        postgres.execute('SET ROLE "user"')
        postgres.execute(f"SET app.claim_schemas = '{schema}'".encode())
        assert execute_sql(
            postgres, "postgres/select_scoped_ids", mapping={"schema": schema}
        ).fetchall() == [("visible",)]
        postgres.execute("SET app.claim_schemas = 'other'")
        assert (
            execute_sql(
                postgres, "postgres/select_scoped_ids", mapping={"schema": schema}
            ).fetchall()
            == []
        )

    def test_schema_scope_predicate_checks_the_mirrored_schemas_claim(
        self,
    ) -> None:
        """
        GIVEN: a schema name.
        WHEN: schema_scope_predicate is rendered.
        THEN: it checks the schema against the mirrored schemas claim.
        """
        rendered = schema_scope_predicate("app").as_string(None)

        assert "'app'" in rendered
        assert "'app.claim_schemas'" in rendered
        assert "string_to_array" in rendered

    def test_bootstrap_requires_a_configured_claim_for_protected_tables(
        self,
    ) -> None:
        """
        GIVEN: a protected table without a configured schema claim.
        WHEN: bootstrap_table is called.
        THEN: it raises RuntimeError.
        """
        with pytest.raises(RuntimeError, match="identity claim"):
            bootstrap_table(
                MagicMock(spec=Connection),
                schema="app",
                table_name="table",
                rls=[UnitMapping(column="id_cras", unit_type="cras")],
                claim=None,
            )

    def test_claim_session_var_maps_to_generic_session_variable_name(
        self,
    ) -> None:
        """
        GIVEN: a claim name.
        WHEN: claim_session_var is rendered.
        THEN: it maps to the generic `app.claim_<name>` session variable.
        """
        assert (
            claim_session_var("preferred_username").as_string(None)
            == "'app.claim_preferred_username'"
        )
