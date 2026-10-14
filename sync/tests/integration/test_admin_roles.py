import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege
from psycopg.sql import SQL, Identifier

from tests.helpers import rows

LEVELS = ["console_viewer", "console_operator", "console_editor", "console_admin"]


async def become(connection: psycopg.AsyncConnection, role: str) -> None:
    """Switch the session to one role, as PostgREST does for a JWT."""
    await connection.execute(SQL("SET ROLE {}").format(Identifier(role)))


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
            pytest.param("SELECT 1 FROM data_proxy.revisions", id="revisions-table"),
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

        with pytest.raises(InsufficientPrivilege):
            await admin_db.execute(statement.encode())

    async def test_a_role_outside_the_levels_reads_nothing(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A role without a level has no access to the admin schema."""
        await admin_db.execute(b"CREATE ROLE outsider")
        await become(admin_db, "outsider")

        with pytest.raises(InsufficientPrivilege):
            await admin_db.execute(b"SELECT * FROM admin.runs")
