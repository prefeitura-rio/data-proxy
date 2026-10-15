import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege

from tests.helpers import become, rows

SEED = """
INSERT INTO admin.schemas (name, claim, encrypted)
VALUES ('alpha', 'sub', FALSE), ('beta', 'sub', FALSE), ('secret', 'sub', TRUE);
INSERT INTO admin.tables (name, schema)
VALUES ('p.d.a1', 'alpha'), ('p.d.b1', 'beta'), ('p.d.s1', 'secret');
CREATE SCHEMA alpha;
CREATE SCHEMA beta;
CREATE SCHEMA secret;
CREATE FUNCTION alpha.dl_table_files(p_table text)
RETURNS TABLE (path text, size_bytes bigint, delete_file text)
LANGUAGE sql AS $$ SELECT 's3://b/alpha/' || p_table || '.parquet', 10::bigint, NULL::text $$;
CREATE FUNCTION beta.dl_table_files(p_table text)
RETURNS TABLE (path text, size_bytes bigint, delete_file text)
LANGUAGE sql AS $$ SELECT 's3://b/beta/' || p_table || '.parquet', 20::bigint, NULL::text $$;
CREATE FUNCTION secret.dl_table_files(p_table text)
RETURNS TABLE (path text, size_bytes bigint, delete_file text)
LANGUAGE sql AS $$ SELECT 's3://b/secret/' || p_table || '.parquet', 30::bigint, NULL::text $$;
"""

FILES = "SELECT * FROM admin.table_files(%s, %s)"


@pytest.fixture
async def seeded(admin_db: psycopg.AsyncConnection) -> psycopg.AsyncConnection:
    """Provide a connection with three schemas, each with a table and a file function."""
    await admin_db.execute(SEED.encode())
    return admin_db


async def files(
    connection: psycopg.AsyncConnection, schema: str, table: str
) -> list[tuple[object, ...]]:
    """Ask for the files of one table."""
    cursor = await connection.execute(FILES.encode(), (schema, table))
    return await cursor.fetchall()


@pytest.mark.postgres
class TestTableFiles:
    """Schema admins list the files of their tables, so the browser can query them."""

    @pytest.mark.parametrize("role", ["console_editor", "console_admin"])
    async def test_lists_the_files_of_a_table_of_the_token(
        self, seeded: psycopg.AsyncConnection, role: str
    ) -> None:
        """The path is the file of the table, named by the last part of its name."""
        await become(seeded, role, "alpha")

        assert await files(seeded, "alpha", "p.d.a1") == [
            ("s3://b/alpha/a1.parquet", 10, None)
        ]

    async def test_the_admin_lists_the_files_of_any_schema(
        self, seeded: psycopg.AsyncConnection
    ) -> None:
        """The admin ignores the schemas of the token."""
        await become(seeded, "console_admin", "alpha")

        assert await files(seeded, "beta", "p.d.b1") == [
            ("s3://b/beta/b1.parquet", 20, None)
        ]

    @pytest.mark.parametrize("role", ["console_viewer", "console_operator"])
    async def test_lower_levels_cannot_list_files(
        self, seeded: psycopg.AsyncConnection, role: str
    ) -> None:
        """Raw files hold every row, so only editors and admins get them."""
        await become(seeded, role, "alpha")

        with pytest.raises(InsufficientPrivilege):
            await files(seeded, "alpha", "p.d.a1")

    @pytest.mark.parametrize(
        ("schema", "table"),
        [
            pytest.param("beta", "p.d.b1", id="schema-of-another-token"),
            pytest.param("alpha", "p.d.b1", id="table-of-another-schema"),
            pytest.param("alpha", "p.d.none", id="unknown-table"),
            pytest.param("none", "p.d.a1", id="unknown-schema"),
        ],
    )
    async def test_an_editor_cannot_list_outside_its_schemas(
        self, seeded: psycopg.AsyncConnection, schema: str, table: str
    ) -> None:
        """A name outside the token, or one that doesn't exist, gets the same refusal."""
        await become(seeded, "console_editor", "alpha")

        with pytest.raises(InsufficientPrivilege):
            await files(seeded, schema, table)

    async def test_an_encrypted_schema_is_a_conflict(
        self, seeded: psycopg.AsyncConnection
    ) -> None:
        """The browser has no key for the files, so it gets a 409 with the reason."""
        await become(seeded, "console_editor", "secret")

        with pytest.raises(psycopg.Error) as error:
            await files(seeded, "secret", "p.d.s1")

        assert error.value.sqlstate == "PT409"

    async def test_the_function_is_not_open_to_the_public(
        self, seeded: psycopg.AsyncConnection
    ) -> None:
        """Only the editor level and above can run the function."""
        result = await rows(
            seeded,
            "SELECT has_function_privilege('console_viewer', 'admin.table_files(text, text)', 'EXECUTE'), has_function_privilege('console_editor', 'admin.table_files(text, text)', 'EXECUTE')",
        )

        assert result == [(False, True)]
