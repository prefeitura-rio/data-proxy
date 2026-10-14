import psycopg
import pytest
from psycopg.errors import CheckViolation, InsufficientPrivilege
from psycopg.types.json import Jsonb

from tests.helpers import become, rows

DOCUMENT: dict[str, object] = {
    "schemas": {"alpha": {"source": {"type": "bigquery"}, "tables": []}}
}
OTHER: dict[str, object] = {"schemas": {}}
CLAIMS = "SELECT set_config('request.jwt.claims', %s, true)"


async def claims(connection: psycopg.AsyncConnection, value: str) -> None:
    """Set the JWT claims of the request, as PostgREST does."""
    await connection.execute(CLAIMS.encode(), (value,))


async def draft(connection: psycopg.AsyncConnection, comment: str) -> None:
    """Insert one validated draft as the table owner, as the worker leaves it."""
    await connection.execute(
        b"""
        INSERT INTO data_proxy.revisions (author, comment, document, valid, validated_at)
        VALUES ('ana', %s, %s, TRUE, now())
        """,
        (comment, Jsonb(DOCUMENT)),
    )


@pytest.mark.postgres
class TestDrafts:
    """Editors write draft configurations, and only drafts."""

    @pytest.mark.parametrize(
        ("jwt", "author"),
        [
            pytest.param(
                '{"preferred_username": "ana", "sub": "u1"}', "ana", id="username"
            ),
            pytest.param('{"sub": "u1"}', "u1", id="subject"),
            pytest.param("", "console_editor", id="no-token"),
        ],
    )
    async def test_a_draft_takes_its_author_from_the_token(
        self, admin_db: psycopg.AsyncConnection, jwt: str, author: str
    ) -> None:
        """The author is the username claim, then the subject, then the database role."""
        await claims(admin_db, jwt)
        await become(admin_db, "console_editor")

        await admin_db.execute(
            b"INSERT INTO admin.drafts (comment, document) VALUES ('new', %s)",
            (Jsonb(DOCUMENT),),
        )

        result = await rows(
            admin_db,
            "SELECT author, comment, valid, activated_at FROM admin.revisions WHERE comment = 'new'",
        )
        assert result == [(author, "new", None, None)]

    @pytest.mark.parametrize("role", ["console_editor", "console_admin"])
    async def test_editors_and_admins_write_drafts(
        self, admin_db: psycopg.AsyncConnection, role: str
    ) -> None:
        """The editor level and the admin level, which inherits it, can write drafts."""
        await become(admin_db, role)

        cursor = await admin_db.execute(
            b"INSERT INTO admin.drafts (comment, document) VALUES ('x', %s)",
            (Jsonb(DOCUMENT),),
        )

        assert cursor.rowcount == 1

    @pytest.mark.parametrize("role", ["console_viewer", "console_operator"])
    async def test_lower_levels_cannot_write_drafts(
        self, admin_db: psycopg.AsyncConnection, role: str
    ) -> None:
        """Viewers and operators can read drafts but not write them."""
        await become(admin_db, role)

        with pytest.raises(InsufficientPrivilege):
            await admin_db.execute(
                b"INSERT INTO admin.drafts (comment, document) VALUES ('x', %s)",
                (Jsonb(DOCUMENT),),
            )

    async def test_an_editor_cannot_choose_the_author(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Only the token names the author of a draft."""
        await become(admin_db, "console_editor")

        with pytest.raises(InsufficientPrivilege):
            await admin_db.execute(
                b"INSERT INTO admin.drafts (author, comment, document) VALUES ('bob', 'x', %s)",
                (Jsonb(DOCUMENT),),
            )

    async def test_a_document_without_schemas_is_rejected(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A draft must hold a schemas object."""
        await become(admin_db, "console_editor")

        with pytest.raises(CheckViolation):
            await admin_db.execute(
                b"INSERT INTO admin.drafts (comment, document) VALUES ('x', '{}')"
            )

    async def test_changing_the_document_clears_the_validation(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A new document needs a new validation, and a new comment doesn't."""
        await draft(admin_db, "checked")
        await become(admin_db, "console_editor")

        await admin_db.execute(b"UPDATE admin.drafts SET comment = 'renamed'")
        kept = await rows(
            admin_db, "SELECT valid FROM admin.drafts WHERE comment = 'renamed'"
        )
        await admin_db.execute(
            b"UPDATE admin.drafts SET document = %s", (Jsonb(OTHER),)
        )
        cleared = await rows(
            admin_db,
            "SELECT valid, errors, validated_at FROM admin.drafts WHERE comment = 'renamed'",
        )

        assert (kept, cleared) == ([(True,)], [(None, None, None)])

    async def test_an_editor_can_delete_a_draft(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A draft can be deleted."""
        await draft(admin_db, "gone")
        await become(admin_db, "console_editor")

        cursor = await admin_db.execute(b"DELETE FROM admin.drafts")

        assert cursor.rowcount == 1

    async def test_activated_revisions_are_not_drafts(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Editors can't change or delete a revision that was ever active."""
        await admin_db.execute(b"DELETE FROM data_proxy.revisions")
        await admin_db.execute(
            b"""
            INSERT INTO data_proxy.revisions (author, comment, document, valid, active, activated_at)
            VALUES ('ana', 'old', %s, TRUE, FALSE, now()), ('ana', 'live', %s, TRUE, TRUE, now())
            """,
            (Jsonb(DOCUMENT), Jsonb(DOCUMENT)),
        )
        await become(admin_db, "console_editor")

        updated = await admin_db.execute(b"UPDATE admin.drafts SET comment = 'x'")
        deleted = await admin_db.execute(b"DELETE FROM admin.drafts")

        assert (updated.rowcount, deleted.rowcount) == (0, 0)
