import psycopg
import pytest
from psycopg.errors import (
    CheckViolation,
    ForeignKeyViolation,
    InsufficientPrivilege,
    UniqueViolation,
)

from tests.helpers import become, rows

CLAIMS = "SELECT set_config('request.jwt.claims', %s, true)"


async def seed(connection: psycopg.AsyncConnection) -> None:
    """Add one schema with a claim, one schema without, and one table with a rule."""
    await connection.execute(
        b"INSERT INTO admin.schemas (name, claim) VALUES ('alpha', 'preferred_username'), ('beta', NULL)"
    )
    await connection.execute(
        b"INSERT INTO admin.tables (schema, name) VALUES ('alpha', 'p.d.one')"
    )
    await connection.execute(
        b"INSERT INTO admin.rls (table_name, column_name, unit_type) VALUES ('p.d.one', 'unit_id', 'unit')"
    )


@pytest.mark.postgres
class TestConfigWrites:
    """Editors write the configuration tables, and the database stamps every change."""

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
    async def test_every_change_records_who_made_it(
        self, admin_db: psycopg.AsyncConnection, jwt: str, author: str
    ) -> None:
        """The name comes from the token, and an editor can't choose it."""
        await admin_db.execute(CLAIMS.encode(), (jwt,))
        await become(admin_db, "console_editor")

        await admin_db.execute(b"INSERT INTO admin.schemas (name) VALUES ('alpha')")
        await admin_db.execute(
            b"INSERT INTO admin.tables (schema, name) VALUES ('alpha', 'p.d.one')"
        )
        await admin_db.execute(b"UPDATE admin.tables SET cache_ttl = 5")

        result = await rows(
            admin_db,
            "SELECT s.updated_by, t.updated_by FROM admin.schemas AS s, admin.tables AS t",
        )
        assert result == [(author, author)]

    async def test_a_change_moves_the_update_time_forward(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """updated_at follows each update."""
        await seed(admin_db)
        await admin_db.execute(
            b"UPDATE admin.tables SET updated_at = '2020-01-01' WHERE name = 'p.d.one'"
        )
        await become(admin_db, "console_editor")

        await admin_db.execute(b"UPDATE admin.tables SET cache_ttl = 5")

        result = await rows(
            admin_db,
            "SELECT updated_at > now() - interval '1 minute' FROM admin.tables",
        )
        assert result == [(True,)]

    @pytest.mark.parametrize(
        ("statement", "error"),
        [
            pytest.param(
                "INSERT INTO admin.schemas (name) VALUES ('Bad-Name')",
                CheckViolation,
                id="schema-name",
            ),
            pytest.param(
                "INSERT INTO admin.schemas (name, source) VALUES ('x', '')",
                CheckViolation,
                id="empty-source",
            ),
            pytest.param(
                "INSERT INTO admin.schemas (name, settings) VALUES ('x', '{}')",
                CheckViolation,
                id="empty-settings",
            ),
            pytest.param(
                "INSERT INTO admin.schemas (name, claim) VALUES ('x', '')",
                CheckViolation,
                id="empty-claim",
            ),
            pytest.param(
                "INSERT INTO admin.schemas (name) VALUES ('alpha')",
                UniqueViolation,
                id="duplicate-schema",
            ),
            pytest.param(
                "INSERT INTO admin.tables (schema, name) VALUES ('missing', 'p.d.x')",
                ForeignKeyViolation,
                id="unknown-schema",
            ),
            pytest.param(
                "INSERT INTO admin.tables (schema, name, strategy) VALUES ('alpha', 'p.d.x', 'nope')",
                CheckViolation,
                id="unknown-strategy",
            ),
            pytest.param(
                "INSERT INTO admin.tables (schema, name, n) VALUES ('alpha', 'p.d.x', 3)",
                CheckViolation,
                id="window-on-a-full-table",
            ),
            pytest.param(
                "INSERT INTO admin.tables (schema, name, fallback) VALUES ('alpha', 'p.d.x', TRUE)",
                CheckViolation,
                id="fallback-on-a-full-table",
            ),
            pytest.param(
                "INSERT INTO admin.tables (schema, name, strategy, n) VALUES ('alpha', 'p.d.x', 'partitioned', 0)",
                CheckViolation,
                id="zero-window",
            ),
            pytest.param(
                "INSERT INTO admin.tables (schema, name, cache_ttl) VALUES ('alpha', 'p.d.x', -1)",
                CheckViolation,
                id="negative-cache-lifetime",
            ),
            pytest.param(
                "INSERT INTO admin.tables (schema, name, sort) VALUES ('alpha', 'p.d.x', '{}')",
                CheckViolation,
                id="empty-sort",
            ),
            pytest.param(
                "INSERT INTO admin.tables (schema, name, partitioning) VALUES ('alpha', 'p.d.x', '{}')",
                CheckViolation,
                id="partitioning-not-a-list",
            ),
            pytest.param(
                "INSERT INTO admin.tables (schema, name) VALUES ('beta', 'p.d.one')",
                UniqueViolation,
                id="table-in-two-schemas",
            ),
            pytest.param(
                "INSERT INTO admin.tables (schema, name) VALUES ('alpha', 'q.e.one')",
                UniqueViolation,
                id="same-destination-name",
            ),
            pytest.param(
                "INSERT INTO admin.rls (table_name, column_name, unit_type) VALUES ('p.d.one', 'unit_id', 'unit')",
                UniqueViolation,
                id="duplicate-rule",
            ),
            pytest.param(
                "INSERT INTO admin.tables (schema, name) VALUES ('beta', 'p.d.two'); INSERT INTO admin.rls (table_name, column_name, unit_type) VALUES ('p.d.two', 'c', 'u')",
                CheckViolation,
                id="rule-without-a-claim",
            ),
            pytest.param(
                "UPDATE admin.schemas SET claim = NULL WHERE name = 'alpha'",
                CheckViolation,
                id="claim-removed-while-a-rule-needs-it",
            ),
        ],
    )
    async def test_the_database_refuses_a_bad_configuration(
        self,
        admin_db: psycopg.AsyncConnection,
        statement: str,
        error: type[psycopg.Error],
    ) -> None:
        """Each rule of the sync model that SQL can enforce has a constraint."""
        await seed(admin_db)
        await become(admin_db, "console_editor")

        with pytest.raises(error):
            await admin_db.execute(statement.encode())


@pytest.mark.postgres
class TestConfigPrivileges:
    """Editors insert and update, admins also delete, and keys never change."""

    @pytest.mark.parametrize("role", ["console_viewer", "console_operator"])
    async def test_lower_levels_cannot_write(
        self, admin_db: psycopg.AsyncConnection, role: str
    ) -> None:
        """Viewers and operators only read the configuration."""
        await become(admin_db, role)

        with pytest.raises(InsufficientPrivilege):
            await admin_db.execute(b"INSERT INTO admin.schemas (name) VALUES ('x')")

    @pytest.mark.parametrize(
        "statement",
        [
            pytest.param("UPDATE admin.schemas SET name = 'other'", id="rename-schema"),
            pytest.param("UPDATE admin.tables SET name = 'other'", id="rename-table"),
            pytest.param("UPDATE admin.tables SET schema = 'beta'", id="move-table"),
            pytest.param("UPDATE admin.schemas SET updated_by = 'bob'", id="author"),
            pytest.param("DELETE FROM admin.tables", id="editor-delete"),
        ],
    )
    async def test_editors_cannot_change_keys_the_author_or_delete(
        self, admin_db: psycopg.AsyncConnection, statement: str
    ) -> None:
        """Identity columns, the stamp, and deletes stay out of reach for editors."""
        await seed(admin_db)
        await become(admin_db, "console_editor")

        with pytest.raises(InsufficientPrivilege):
            await admin_db.execute(statement.encode())

    async def test_admins_delete_in_order_and_a_table_takes_its_rules_along(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A schema with tables is protected, and deleting a table deletes its rules."""
        await seed(admin_db)
        await become(admin_db, "console_admin")

        with pytest.raises(ForeignKeyViolation):
            async with admin_db.transaction():
                await admin_db.execute(
                    b"DELETE FROM admin.schemas WHERE name = 'alpha'"
                )
        await admin_db.execute(b"DELETE FROM admin.tables")

        result = await rows(admin_db, "SELECT count(*) FROM admin.rls")
        assert result == [(0,)]
