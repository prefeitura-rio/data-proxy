import psycopg
import pytest
from psycopg.errors import (
    FeatureNotSupported,
    InsufficientPrivilege,
    ObjectNotInPrerequisiteState,
)
from psycopg.sql import SQL, Identifier, Literal

from tests.helpers import become, rows

LEVELS = ["console_viewer", "console_operator", "console_editor", "console_admin"]


@pytest.mark.postgres
class TestAdminRoles:
    """Each JWT level reads the admin views and nothing else."""

    @pytest.mark.parametrize("role", LEVELS)
    async def test_every_level_reads_every_admin_view(
        self, admin_db: psycopg.AsyncConnection, role: str
    ) -> None:
        """Every level inherits the viewer grants on all views of the admin schema."""
        views = [
            name
            for (name,) in await rows(
                admin_db,
                "SELECT viewname FROM pg_views WHERE schemaname = 'admin'",
            )
        ]
        await become(admin_db, role)

        for view in views:
            await admin_db.execute(
                SQL("SELECT * FROM admin.{} LIMIT 0").format(Identifier(str(view)))
            )

        assert views

    @pytest.mark.parametrize(
        "statement",
        [
            pytest.param("SELECT 1 FROM dbos.workflow_status", id="dbos-table"),
            pytest.param("SELECT 1 FROM data_proxy.state", id="state-table"),
            pytest.param(
                "INSERT INTO admin.errors (reason, fields) VALUES ('x', '{}')",
                id="insert-into-view",
            ),
            pytest.param("DELETE FROM admin.errors", id="delete-from-view"),
        ],
    )
    async def test_the_viewer_can_only_read_the_views(
        self, admin_db: psycopg.AsyncConnection, statement: str
    ) -> None:
        """A viewer can't read the tables behind the views or change anything."""
        await become(admin_db, "console_viewer")

        with pytest.raises(
            (
                InsufficientPrivilege,
                FeatureNotSupported,
                ObjectNotInPrerequisiteState,
            )
        ):
            await admin_db.execute(statement.encode())

    async def test_a_role_outside_the_levels_reads_nothing(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A role without a level has no access to the admin schema."""
        await admin_db.execute(b"CREATE ROLE outsider")
        await become(admin_db, "outsider")

        with pytest.raises(InsufficientPrivilege):
            await admin_db.execute(b"SELECT * FROM admin.runs")


@pytest.mark.postgres
class TestRoleSettings:
    """The roles that PostgREST switches to have the same safeguards as the data API roles."""

    @pytest.mark.parametrize("role", LEVELS)
    async def test_every_level_has_a_statement_timeout(
        self, admin_db: psycopg.AsyncConnection, role: str
    ) -> None:
        """PostgREST applies the settings of the role of the request, and the levels don't inherit them."""
        result = await rows(
            admin_db,
            SQL("SELECT setconfig FROM pg_db_role_setting WHERE setrole = {}::regrole")
            .format(Literal(role))
            .as_string(),
        )

        assert any("statement_timeout=30s" in str(row[0]) for row in result)

    @pytest.mark.parametrize("role", ["anon", *LEVELS])
    @pytest.mark.parametrize(
        "function",
        [
            pytest.param("data_proxy.reload_postgrest()", id="reload"),
            pytest.param("data_proxy.wait_for_standbys(interval)", id="wait"),
        ],
    )
    async def test_request_roles_cannot_run_the_reload_functions(
        self, admin_db: psycopg.AsyncConnection, role: str, function: str
    ) -> None:
        """The roles of the API have usage on the schema, and the functions must not be open to them."""
        result = await rows(
            admin_db,
            SQL("SELECT has_function_privilege({}, {}, 'EXECUTE')")
            .format(Literal(role), Literal(function))
            .as_string(),
        )

        assert result == [(False,)]
