import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege
from psycopg.types.json import Jsonb

from tests.helpers import become, rows

LIVE: dict[str, object] = {"schemas": {"live": {"tables": []}}}
NEXT: dict[str, object] = {"schemas": {"next": {"tables": []}}}


async def revision(
    connection: psycopg.AsyncConnection,
    comment: str,
    document: dict[str, object],
    *,
    valid: bool | None,
    active: bool = False,
    activated: bool = False,
) -> int:
    """Insert one revision as the table owner and return its id."""
    cursor = await connection.execute(
        b"""
        INSERT INTO data_proxy.revisions (author, comment, document, valid, active, activated_at)
        VALUES ('ana', %s, %s, %s, %s, CASE WHEN %s THEN now() END)
        RETURNING id
        """,
        (comment, Jsonb(document), valid, active, activated or active),
    )
    row = await cursor.fetchone()
    assert row is not None
    identifier = row[0]
    assert isinstance(identifier, int)
    return identifier


async def activate(connection: psycopg.AsyncConnection, identifier: int) -> None:
    """Call the activation function as PostgREST does for an RPC."""
    await connection.execute(b"SELECT admin.activate_revision(%s)", (identifier,))


async def attempt(connection: psycopg.AsyncConnection, identifier: int) -> str | None:
    """Try an activation inside a savepoint and return the SQLSTATE of its error."""
    try:
        async with connection.transaction():
            await activate(connection, identifier)
    except psycopg.Error as error:
        return error.sqlstate
    return None


@pytest.fixture
async def live(admin_db: psycopg.AsyncConnection) -> int:
    """Start from one active revision."""
    await admin_db.execute(b"DELETE FROM data_proxy.revisions")
    return await revision(admin_db, "live", LIVE, valid=True, active=True)


@pytest.mark.postgres
class TestActivation:
    """Admins activate validated revisions, one at a time."""

    @pytest.mark.usefixtures("live")
    async def test_activating_a_valid_draft_replaces_the_active_revision(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """The new revision becomes the config, and the old one stays in the history."""
        draft = await revision(admin_db, "next", NEXT, valid=True)
        await become(admin_db, "console_admin")

        await activate(admin_db, draft)

        result = await rows(
            admin_db,
            "SELECT comment, active, activated_at IS NOT NULL FROM admin.revisions ORDER BY id",
        )
        config = await rows(admin_db, "SELECT id, document FROM admin.config")
        assert (result, config) == (
            [("live", False, True), ("next", True, True)],
            [(draft, NEXT)],
        )

    async def test_an_earlier_revision_can_be_activated_again(
        self, admin_db: psycopg.AsyncConnection, live: int
    ) -> None:
        """A rollback activates the earlier revision, which was validated before."""
        draft = await revision(admin_db, "next", NEXT, valid=True)
        await become(admin_db, "console_admin")
        await activate(admin_db, draft)

        await activate(admin_db, live)

        config = await rows(admin_db, "SELECT id FROM admin.config")
        assert config == [(live,)]

    @pytest.mark.parametrize("valid", [None, False], ids=["unvalidated", "invalid"])
    async def test_a_revision_that_is_not_valid_is_refused(
        self, admin_db: psycopg.AsyncConnection, live: int, valid: bool | None
    ) -> None:
        """Only a revision that passed validation can become active, and the active one stays."""
        draft = await revision(admin_db, "next", NEXT, valid=valid)
        await become(admin_db, "console_admin")

        state = await attempt(admin_db, draft)

        config = await rows(admin_db, "SELECT id FROM admin.config")
        assert (state, config) == ("PT400", [(live,)])

    @pytest.mark.usefixtures("live")
    async def test_an_unknown_revision_is_not_found(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A missing revision gives a not found error."""
        await become(admin_db, "console_admin")

        assert await attempt(admin_db, 0) == "PT404"

    @pytest.mark.usefixtures("live")
    @pytest.mark.parametrize(
        "role", ["console_viewer", "console_operator", "console_editor"]
    )
    async def test_only_admins_can_activate(
        self, admin_db: psycopg.AsyncConnection, role: str
    ) -> None:
        """The lower levels have no right to run the function."""
        draft = await revision(admin_db, "next", NEXT, valid=True)
        await become(admin_db, role)

        with pytest.raises(InsufficientPrivilege):
            await activate(admin_db, draft)
