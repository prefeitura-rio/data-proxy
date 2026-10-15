import pytest
from psycopg import AsyncConnection
from psycopg.errors import InsufficientPrivilege
from psycopg.sql import Identifier
from pydantic import TypeAdapter

from sync.settings import settings
from tests.helpers import become, helm_sql, rows

RELOADS = "SELECT count(*), min(queue_name) FROM dbos.workflow_status WHERE name = 'reload_proxy'"


async def config(connection: AsyncConnection) -> dict[str, object]:
    """Read the document that the proxy gets."""
    result = await rows(connection, "SELECT admin.config()")
    document = result[0][0]
    return TypeAdapter(dict[str, object]).validate_python(document)


@pytest.mark.postgres
class TestProxyConfig:
    """The proxy gets only what it needs: the cache lifetime of each table."""

    async def test_an_empty_configuration_has_no_schemas(
        self, admin_db: AsyncConnection
    ) -> None:
        """Without rows the document is valid and empty."""
        assert await config(admin_db) == {"schemas": {}}

    async def test_the_document_has_table_names_and_cache_lifetimes_only(
        self, admin_db: AsyncConnection
    ) -> None:
        """Claims, rules, and strategies stay out of the proxy."""
        await admin_db.execute(
            b"INSERT INTO admin.schemas (name, claim) VALUES ('alpha', 'sub')"
        )
        await admin_db.execute(
            b"INSERT INTO admin.tables (name, schema, cache_ttl) VALUES ('p.d.one', 'alpha', 90), ('p.d.two', 'alpha', NULL)"
        )
        await admin_db.execute(
            b"INSERT INTO admin.rls (table_name, column_name, unit_type) VALUES ('p.d.one', 'unit_id', 'unit')"
        )

        assert await config(admin_db) == {
            "schemas": {
                "alpha": {
                    "tables": [
                        {"name": "p.d.one", "cache_ttl": 90},
                        {"name": "p.d.two"},
                    ]
                }
            }
        }

    async def test_a_schema_without_tables_is_listed_with_none(
        self, admin_db: AsyncConnection
    ) -> None:
        """An empty schema appears, so the proxy knows nothing is cached there."""
        await admin_db.execute(b"INSERT INTO admin.schemas (name) VALUES ('beta')")

        assert await config(admin_db) == {"schemas": {"beta": {"tables": []}}}

    async def test_the_anonymous_role_reads_the_document_and_nothing_else(
        self, admin_db: AsyncConnection
    ) -> None:
        """The chart grants the one function that the proxy calls, and no table or other function."""
        await admin_db.execute(
            helm_sql(
                "grant_proxy_config",
                {"anonymous_role": Identifier(settings.AUTH_ANON_ROLE)},
            ).encode()
        )
        await become(admin_db, settings.AUTH_ANON_ROLE)

        document = await rows(admin_db, "SELECT admin.config()")
        assert document == [({"schemas": {}},)]

        for denied in ("SELECT admin.sync_config()", "SELECT * FROM admin.runs"):
            with pytest.raises(InsufficientPrivilege):
                async with admin_db.transaction():
                    await rows(admin_db, denied)


@pytest.mark.postgres
class TestReloadQueue:
    """Every change of a configuration table queues one reload of the proxy."""

    @pytest.mark.parametrize(
        "statement",
        [
            pytest.param(
                "INSERT INTO admin.schemas (name) VALUES ('alpha')", id="schema"
            ),
            pytest.param(
                "INSERT INTO admin.schemas (name, claim) VALUES ('alpha', 'sub'); INSERT INTO admin.tables (name, schema) VALUES ('p.d.one', 'alpha')",
                id="table",
            ),
            pytest.param(
                "INSERT INTO admin.schemas (name, claim) VALUES ('alpha', 'sub'); INSERT INTO admin.tables (name, schema) VALUES ('p.d.one', 'alpha'); INSERT INTO admin.rls (table_name, column_name, unit_type) VALUES ('p.d.one', 'c', 'u')",
                id="rule",
            ),
        ],
    )
    async def test_a_change_queues_a_reload(
        self, admin_db: AsyncConnection, statement: str
    ) -> None:
        """The reload goes to the proxy queue, whichever table changed."""
        await admin_db.execute(statement.encode())

        assert await rows(admin_db, RELOADS) == [(1, "proxy")]

    @pytest.mark.parametrize("role", ["console_editor", "console_admin"])
    async def test_a_burst_of_edits_queues_one_reload_and_never_fails_a_write(
        self, admin_db: AsyncConnection, role: str
    ) -> None:
        """Edits that arrive before the reload runs share it, and the editor sees no error."""
        await become(admin_db, role, "alpha")

        await admin_db.execute(b"INSERT INTO admin.schemas (name) VALUES ('alpha')")
        await admin_db.execute(
            b"INSERT INTO admin.tables (name, schema) VALUES ('p.d.one', 'alpha')"
        )
        await admin_db.execute(b"UPDATE admin.tables SET cache_ttl = 5")
        await admin_db.execute(b"RESET ROLE")

        assert await rows(admin_db, RELOADS) == [(1, "proxy")]

    async def test_a_deleted_row_queues_a_reload(
        self, admin_db: AsyncConnection
    ) -> None:
        """A delete changes the configuration too."""
        await admin_db.execute(b"INSERT INTO admin.schemas (name) VALUES ('alpha')")
        await admin_db.execute(b"DELETE FROM dbos.workflow_status")

        await admin_db.execute(b"DELETE FROM admin.schemas")

        assert await rows(admin_db, RELOADS) == [(1, "proxy")]
