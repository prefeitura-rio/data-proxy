import pytest
from psycopg import Connection

from dp.authorization import (
    bootstrap_table,
    claim_session_var,
    schema_scope_predicate,
)
from dp.models import UnitMapping
from dp.templates import TemplateSpec
from tests.helpers import execute_sql, execute_template


class TestAuthorization:
    """Tests for authorization validation and bootstrap safety."""

    def test_bootstrap_rejects_an_invalid_runtime_rls_value(
        self,
        postgres: Connection[tuple[object, ...]],
        invalid_rls: list[UnitMapping],
    ) -> None:
        """
        GIVEN: an invalid runtime RLS value.
        WHEN: bootstrap_table is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            bootstrap_table(postgres, "app", "table", invalid_rls, None)

    def test_bootstrap_grants_access_without_rls(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a non-RLS table.
        WHEN: bootstrap_table is called.
        THEN: it receives a read grant and a schema-scope policy.
        """
        execute_template(
            postgres,
            TemplateSpec(
                path="postgres/create_table",
                mapping={
                    "schema": "app",
                    "table": "table",
                    "columns": "id_cras text",
                },
            ),
        )

        bootstrap_table(
            postgres,
            schema="app",
            table_name="table",
            rls=None,
            claim=None,
        )

        assert execute_sql(postgres, "postgres/relrowsecurity").fetchone() == (True,)
        assert execute_sql(postgres, "postgres/policy_names").fetchall() == [
            ("schema_scoped",)
        ]
        assert execute_sql(postgres, "postgres/select_grants").fetchall() == [("user",)]

    def test_bootstrap_installs_access_policy_check(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a protected table with RLS and an access_policy table.
        WHEN: bootstrap_table is called.
        THEN: it renders grants and the access_policy check together.
        """
        execute_template(
            postgres,
            TemplateSpec(
                path="postgres/create_table",
                mapping={
                    "schema": "app",
                    "table": "table",
                    "columns": "id_cras text",
                },
            ),
        )
        execute_template(
            postgres,
            TemplateSpec(
                path="postgres/create_access_policy",
                mapping={},
            ),
        )

        bootstrap_table(
            postgres,
            schema="app",
            table_name="table",
            rls=[UnitMapping(column="id_cras", unit_type="cras")],
            claim="preferred_username",
        )

        assert execute_sql(postgres, "postgres/policy_names").fetchall() == [
            ("access_policy_scoped",)
        ]

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
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """
        GIVEN: a protected table without a configured schema claim.
        WHEN: bootstrap_table is called.
        THEN: it raises RuntimeError.
        """
        with pytest.raises(RuntimeError, match="identity claim"):
            bootstrap_table(
                postgres,
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
