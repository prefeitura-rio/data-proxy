import psycopg
import pytest
from psycopg.errors import CheckViolation, InsufficientPrivilege
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from sync.models import SyncConfig
from tests.helpers import become, rows

FULL: dict[str, object] = {
    "schemas": {
        "alpha": {
            "source": {"type": "bigquery"},
            "claim": "preferred_username",
            "tables": [
                {
                    "name": "p.d.full",
                    "strategy": "full",
                    "cache_ttl": 60,
                    "rls": [
                        {"column": "unit_id", "unit_type": "unit"},
                        {"column": "region_id", "unit_type": "region"},
                    ],
                },
                {
                    "name": "p.d.part",
                    "strategy": "partitioned",
                    "n": 4,
                    "fallback": True,
                    "ducklake": {
                        "sort": ["created"],
                        "partitioning": [{"column": "created", "transform": "day"}],
                    },
                },
            ],
        },
        "beta": {
            "source": {"type": "bigquery"},
            "ducklake": {"encrypted": True},
            "tables": [],
        },
    }
}

VERBOSE: dict[str, object] = {
    "schemas": {
        "gamma": {
            "source": {"type": "bigquery"},
            "ducklake": {"encrypted": False},
            "tables": [
                {"name": "p.d.t", "strategy": "full", "ducklake": {}},
                {"name": "p.d.u", "strategy": "partitioned", "fallback": False},
            ],
        }
    }
}


async def export(connection: psycopg.AsyncConnection) -> dict[str, object]:
    """Read the document that consumers get from the endpoint."""
    result = await rows(connection, "SELECT admin.sync_config()")
    document = result[0][0]
    return TypeAdapter(dict[str, object]).validate_python(document)


async def load(
    connection: psycopg.AsyncConnection, document: dict[str, object]
) -> None:
    """Import one document."""
    await connection.execute(b"SELECT admin.import_config(%s)", (Jsonb(document),))


@pytest.mark.postgres
class TestSyncConfig:
    """The endpoint serves the tables in the shape of sync.json."""

    async def test_an_empty_configuration_has_no_schemas(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Without rows the document is valid and empty."""
        assert await export(admin_db) == {"schemas": {}}

    async def test_a_document_comes_back_the_way_it_went_in(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Import then export gives the same document, and the engine accepts it."""
        await load(admin_db, FULL)

        document = await export(admin_db)

        assert document == FULL
        SyncConfig.model_validate(document)

    async def test_source_settings_come_back_as_they_went_in(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """Settings are free-form JSON, and the source of a schema decides what they mean."""
        document: dict[str, object] = {
            "schemas": {
                "delta": {
                    "source": {
                        "type": "bigquery",
                        "settings": {"project": "x", "labels": ["a", "b"]},
                    },
                    "tables": [],
                }
            }
        }
        await load(admin_db, document)

        assert await export(admin_db) == document

    async def test_defaults_do_not_change_the_meaning_of_a_document(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A document that spells out defaults and the exported one mean the same to the engine."""
        await load(admin_db, VERBOSE)

        document = await export(admin_db)

        assert SyncConfig.model_validate(document) == SyncConfig.model_validate(VERBOSE)

    async def test_importing_again_replaces_the_rules_of_a_table(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """A second import updates rows and leaves the rules as the document says."""
        await load(admin_db, FULL)
        changed: dict[str, object] = {
            "schemas": {
                "alpha": {
                    "source": {"type": "bigquery"},
                    "claim": "preferred_username",
                    "tables": [
                        {
                            "name": "p.d.full",
                            "strategy": "full",
                            "rls": [{"column": "other", "unit_type": "unit"}],
                        }
                    ],
                }
            }
        }

        await load(admin_db, changed)

        result = await rows(
            admin_db,
            "SELECT column_name FROM admin.rls WHERE table_name = 'p.d.full'",
        )
        assert result == [("other",)]

    async def test_a_bad_document_changes_nothing(
        self, admin_db: psycopg.AsyncConnection
    ) -> None:
        """The import is one transaction, so a refused row leaves no partial schema."""
        bad: dict[str, object] = {
            "schemas": {
                "delta": {
                    "tables": [{"name": "p.d.x", "strategy": "nope"}],
                }
            }
        }

        with pytest.raises(CheckViolation):
            async with admin_db.transaction():
                await load(admin_db, bad)

        assert await rows(admin_db, "SELECT count(*) FROM admin.schemas") == [(0,)]

    @pytest.mark.parametrize(
        "role", ["console_viewer", "console_operator", "console_editor"]
    )
    async def test_only_admins_import(
        self, admin_db: psycopg.AsyncConnection, role: str
    ) -> None:
        """The import writes everything, so it needs the top level."""
        await become(admin_db, role)

        with pytest.raises(InsufficientPrivilege):
            await load(admin_db, FULL)

    @pytest.mark.parametrize(
        "role",
        ["console_viewer", "console_operator", "console_editor", "console_admin"],
    )
    async def test_every_level_reads_the_endpoint(
        self, admin_db: psycopg.AsyncConnection, role: str
    ) -> None:
        """Every level that can read the tables can read the document."""
        await become(admin_db, role)

        assert await export(admin_db) == {"schemas": {}}
