import json

import pytest
from psycopg.sql import SQL, Identifier
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from sync.models import PartitionedTable, SyncConfig
from sync.state import read_sync_config
from tests.constants import PROXY_CONSOLE_CONFIG
from tests.fixtures.console import add_schema, add_table, as_token, refusal
from tests.fixtures.types import Postgres
from tests.helpers import scalar

INSUFFICIENT = "42501"
CHECK_VIOLATION = "23514"

Document = dict[str, JsonValue]


async def export(pg: Postgres) -> Document:
    """Return the configuration that the console exports."""
    document = await scalar(pg, "SELECT console.sync_config()")
    assert isinstance(document, dict)

    return document


async def preview(pg: Postgres, document: Document) -> list[tuple[str, str, str, str]]:
    """Return what an import of the document would do, as (object, schema, key, action)."""
    cursor = await pg.connection.execute(
        b"SELECT object, schema_name, key, action FROM console.import_preview(%s)",
        (Jsonb(document),),
    )

    return [(a, b, c, d) for a, b, c, d in await cursor.fetchall()]


async def seed(pg: Postgres) -> None:
    """Add two schemas with a claim, two tables, a partitioned table and two row rules."""
    await add_schema(pg, "sales", claim="cpf")
    await add_schema(pg, "hr", claim="cpf")
    await add_table(pg, "p.d.orders", "sales", cache_ttl=60, rls=("cpf", "cpf"))
    await pg.connection.execute(
        "INSERT INTO console.rls (table_name, column_name, unit_type) VALUES ('p.d.orders', 'cnpj', 'cnpj')"
    )
    await add_table(pg, "p.d.staff", "hr")
    await pg.connection.execute(
        """
        INSERT INTO console.tables (name, schema, strategy, n, fallback, partitioning, sort)
        VALUES ('p.d.events', 'sales', 'partitioned', 4, TRUE, '[{"column": "dt"}]', ARRAY['dt', 'id'])
        """
    )


RULES: list[JsonValue] = [
    {"column": "cpf", "unit_type": "cpf"},
    {"column": "cnpj", "unit_type": "cnpj"},
]

EVENTS: Document = {
    "name": "p.d.events",
    "strategy": "partitioned",
    "n": 4,
    "fallback": True,
    "ducklake": {"partitioning": [{"column": "dt"}], "sort": ["dt", "id"]},
}


def hr_schema(claim: str = "cpf") -> Document:
    """Build the export of the schema hr."""
    return {
        "claim": claim,
        "source": {"type": "bigquery"},
        "tables": [{"name": "p.d.staff", "strategy": "full"}],
    }


def sales_schema(ttl: int = 60, rules: list[JsonValue] | None = None) -> Document:
    """Build the export of the schema sales, with the table orders, its cache time and its row rules."""
    orders: Document = {
        "name": "p.d.orders",
        "strategy": "full",
        "cache_ttl": ttl,
        "rls": RULES if rules is None else rules,
    }

    return {"claim": "cpf", "source": {"type": "bigquery"}, "tables": [EVENTS, orders]}


def exported(
    *,
    ttl: int = 60,
    hr_claim: str = "cpf",
    rules: list[JsonValue] | None = None,
    more: Document | None = None,
) -> Document:
    """Build the document that the console exports for the seed rows, with the given changes."""
    schemas: Document = {
        "hr": hr_schema(hr_claim),
        "sales": sales_schema(ttl, rules),
        **({} if more is None else more),
    }

    return {"schemas": schemas}


@pytest.mark.postgres
class TestSyncConfig:
    """console.sync_config() writes the configuration as the sync.json that the worker reads."""

    async def test_writes_each_schema_with_its_claim_its_source_and_its_tables(
        self, console_api: Postgres
    ) -> None:
        """Leave out every value that the table does not set."""
        await seed(console_api)
        await as_token(console_api, "dp_admin", ["*"], database_role=True)

        assert await export(console_api) == exported()

    async def test_marks_an_encrypted_schema_and_keeps_the_source_settings(
        self, console_api: Postgres
    ) -> None:
        """Write the ducklake block only for an encrypted schema."""
        await console_api.connection.execute(
            "INSERT INTO console.schemas (name, source, settings, encrypted) VALUES "
            "('vault', 'bigquery', '{\"project\": \"p\"}', TRUE)"
        )
        await as_token(console_api, "dp_admin", ["*"], database_role=True)

        document = await export(console_api)

        assert document == {
            "schemas": {
                "vault": {
                    "source": {"type": "bigquery", "settings": {"project": "p"}},
                    "ducklake": {"encrypted": True},
                    "tables": [],
                }
            }
        }

    async def test_shows_only_the_schemas_of_the_token(
        self, console_api: Postgres
    ) -> None:
        """Apply the row policies, so a token exports its part of the configuration."""
        await seed(console_api)
        await as_token(console_api, "dp_console", ["hr"], database_role=True)

        assert await export(console_api) == {"schemas": {"hr": hr_schema()}}


@pytest.mark.postgres
class TestImportPreview:
    """console.import_preview() says what an import does, and changes nothing."""

    async def test_an_exported_document_is_all_unchanged(
        self, console_api: Postgres
    ) -> None:
        """Import what the console exports and find no change."""
        await seed(console_api)
        await as_token(console_api, "dp_admin", ["*"], database_role=True)
        document = await export(console_api)

        rows = await preview(console_api, document)

        assert len(rows) > 0
        assert {action for *_, action in rows} == {"unchanged"}

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param(
                exported(ttl=120),
                [("tables", "sales", "p.d.orders", "update")],
                id="cache-ttl-changes",
            ),
            pytest.param(
                exported(hr_claim="other"),
                [("schemas", "hr", "hr", "update")],
                id="claim-changes",
            ),
            pytest.param(
                exported(rules=RULES[:1]),
                [("rls", "sales", "p.d.orders.cnpj", "delete")],
                id="rule-goes-away",
            ),
            pytest.param(
                exported(rules=[*RULES, {"column": "email", "unit_type": "mail"}]),
                [("rls", "sales", "p.d.orders.email", "insert")],
                id="rule-comes",
            ),
            pytest.param(
                exported(rules=[{"column": "cpf", "unit_type": "tax_id"}, RULES[1]]),
                [("rls", "sales", "p.d.orders.cpf", "update")],
                id="rule-changes-its-unit-type",
            ),
            pytest.param(
                exported(
                    more={
                        "new": {
                            "claim": "cpf",
                            "tables": [{"name": "p.d.fresh", "strategy": "full"}],
                        }
                    }
                ),
                [
                    ("schemas", "new", "new", "insert"),
                    ("tables", "new", "p.d.fresh", "insert"),
                ],
                id="schema-and-table-come",
            ),
        ],
    )
    async def test_lists_each_change_of_the_document(
        self,
        console_api: Postgres,
        document: Document,
        expected: list[tuple[str, str, str, str]],
    ) -> None:
        """Name the object, the schema, the key and the action of every row that differs."""
        await seed(console_api)
        await as_token(console_api, "dp_admin", ["*"], database_role=True)
        assert await export(console_api) == exported()

        rows = await preview(console_api, document)

        assert [row for row in rows if row[3] != "unchanged"] == expected

    async def test_changes_nothing(self, console_api: Postgres) -> None:
        """Roll the import back, with its history rows."""
        await seed(console_api)
        await as_token(console_api, "dp_admin", ["*"], database_role=True)
        before = await export(console_api)
        history = await scalar(
            console_api, "SELECT count(*) FROM console.config_history"
        )

        await preview(console_api, exported(ttl=5))

        assert await export(console_api) == before
        assert (
            await scalar(console_api, "SELECT count(*) FROM console.config_history")
            == history
        )

    @pytest.mark.parametrize("function", ["import_preview", "import_config"])
    async def test_needs_the_import_permission(
        self, console_api: Postgres, function: str
    ) -> None:
        """Refuse a token that reads the configuration but may not import."""
        await seed(console_api)
        await as_token(console_api, "dp_console", ["*"], database_role=True)

        state = await refusal(
            console_api, f"SELECT * FROM console.{function}(%s)", Jsonb({"schemas": {}})
        )

        assert state == INSUFFICIENT


@pytest.mark.postgres
class TestImportConfig:
    """console.import_config() writes the document, inside the rights of the token."""

    async def test_an_import_writes_what_the_preview_said(
        self, console_api: Postgres
    ) -> None:
        """Apply the changes of a document to the tables."""
        await seed(console_api)
        await as_token(console_api, "dp_admin", ["*"], database_role=True)
        document = exported(ttl=120, rules=[{"column": "email", "unit_type": "mail"}])
        planned = await preview(console_api, document)

        await console_api.connection.execute(
            b"SELECT console.import_config(%s)", (Jsonb(document),)
        )

        assert (
            await scalar(
                console_api,
                "SELECT cache_ttl FROM console.tables WHERE name = 'p.d.orders'",
            )
            == 120
        )
        cursor = await console_api.connection.execute(
            b"SELECT column_name, unit_type FROM console.rls WHERE table_name = 'p.d.orders'"
        )
        assert await cursor.fetchall() == [("email", "mail")]
        assert [row for row in planned if row[3] != "unchanged"] == [
            ("rls", "sales", "p.d.orders.cnpj", "delete"),
            ("rls", "sales", "p.d.orders.cpf", "delete"),
            ("rls", "sales", "p.d.orders.email", "insert"),
            ("tables", "sales", "p.d.orders", "update"),
        ]

    async def test_importing_an_export_changes_no_value(
        self, console_api: Postgres
    ) -> None:
        """Make the export and the import the two halves of one round trip."""
        await seed(console_api)
        await as_token(console_api, "dp_admin", ["*"], database_role=True)
        before = await export(console_api)

        await console_api.connection.execute(
            b"SELECT console.import_config(%s)", (Jsonb(before),)
        )

        assert await export(console_api) == before

    async def test_a_document_never_deletes_a_schema_or_a_table(
        self, console_api: Postgres
    ) -> None:
        """Keep what the document does not name, and replace only the rules of the tables that it names."""
        await seed(console_api)
        await as_token(console_api, "dp_admin", ["*"], database_role=True)

        await console_api.connection.execute(
            b"SELECT console.import_config(%s)",
            (Jsonb({"schemas": {"sales": {"claim": "cpf", "tables": []}}}),),
        )

        assert await scalar(console_api, "SELECT count(*) FROM console.schemas") == 2
        assert await scalar(console_api, "SELECT count(*) FROM console.tables") == 3
        assert await scalar(console_api, "SELECT count(*) FROM console.rls") == 2

    async def test_refuses_a_schema_outside_the_claim_of_the_token(
        self, console_api: Postgres
    ) -> None:
        """Let the row policies of the tables stop an import that reaches beyond the schemas of the token."""
        await seed(console_api)
        await as_token(console_api, "dp_admin", ["sales"], database_role=True)

        state = await refusal(
            console_api,
            "SELECT console.import_config(%s)",
            Jsonb({"schemas": {"hr": {"claim": "cpf", "tables": []}}}),
        )

        assert state == INSUFFICIENT

    async def test_refuses_a_rule_on_a_schema_with_no_claim(
        self, console_api: Postgres
    ) -> None:
        """Say why: the schema needs a claim before its tables can have row rules."""
        await as_token(console_api, "dp_admin", ["*"], database_role=True)
        document = {
            "schemas": {
                "open": {
                    "tables": [
                        {
                            "name": "p.d.t",
                            "rls": [{"column": "cpf", "unit_type": "cpf"}],
                        }
                    ]
                }
            }
        }

        state = await refusal(
            console_api, "SELECT console.import_config(%s)", Jsonb(document)
        )

        assert state == CHECK_VIOLATION


@pytest.mark.postgres
class TestConfigHistory:
    """A change to the configuration writes a row that says who changed what."""

    async def test_records_an_insert_an_update_and_a_delete_with_the_user_name(
        self, console_api: Postgres
    ) -> None:
        """Keep the old and the new row of a change."""
        await as_token(console_api, "dp_admin", ["*"], user="lia", database_role=True)

        await console_api.connection.execute(
            "INSERT INTO console.schemas (name, claim) VALUES ('sales', 'cpf')"
        )
        await console_api.connection.execute(
            "UPDATE console.schemas SET claim = 'tax' WHERE name = 'sales'"
        )
        await console_api.connection.execute(
            "DELETE FROM console.schemas WHERE name = 'sales'"
        )

        cursor = await console_api.connection.execute(
            b"SELECT action, object, key, changed_by, old_row ->> 'claim', new_row ->> 'claim' "
            b"FROM console.config_history ORDER BY id"
        )
        assert await cursor.fetchall() == [
            ("insert", "schemas", "sales", "lia", None, "cpf"),
            ("update", "schemas", "sales", "lia", "cpf", "tax"),
            ("delete", "schemas", "sales", "lia", "tax", None),
        ]

    async def test_names_a_rule_by_its_table_and_its_column(
        self, console_api: Postgres
    ) -> None:
        """Give the key of a row rule as table.column, in the schema of the table."""
        await seed(console_api)
        await as_token(console_api, "dp_admin", ["*"], database_role=True)

        cursor = await console_api.connection.execute(
            b"SELECT schema_name, key FROM console.config_history WHERE object = 'rls' ORDER BY key"
        )

        assert await cursor.fetchall() == [
            ("sales", "p.d.orders.cnpj"),
            ("sales", "p.d.orders.cpf"),
        ]

    async def test_shows_a_token_the_history_of_its_own_schemas(
        self, console_api: Postgres
    ) -> None:
        """Hide the changes of a schema that the token does not reach."""
        await seed(console_api)
        await as_token(console_api, "dp_console", ["hr"], database_role=True)

        cursor = await console_api.connection.execute(
            b"SELECT DISTINCT schema_name FROM console.config_history"
        )

        assert await cursor.fetchall() == [("hr",)]

    async def test_a_token_that_may_not_read_the_configuration_sees_no_history(
        self, console_api: Postgres
    ) -> None:
        """Apply the permission and not only the schema."""
        await seed(console_api)
        await as_token(console_api, "dp_api", ["*"], database_role=True)

        assert (
            await scalar(console_api, "SELECT count(*) FROM console.config_history")
            == 0
        )


@pytest.mark.postgres
class TestProxyConfig:
    """console.config() is the one document that the proxy reads."""

    async def test_lists_each_table_with_its_ttl_and_the_revision_of_the_rules(
        self, console_api: Postgres
    ) -> None:
        """Leave out the values that the proxy does not read, and the TTL of a table that has none."""
        await seed(console_api)
        revision = await scalar(console_api, "SELECT value FROM rbac.revision")

        config = await scalar(console_api, "SELECT console.config()")

        assert config == {
            "revision": revision,
            "schemas": {
                "hr": {"tables": [{"name": "p.d.staff"}]},
                "sales": {
                    "tables": [
                        {"name": "p.d.events"},
                        {"name": "p.d.orders", "cache_ttl": 60},
                    ]
                },
            },
        }

    async def test_is_the_document_that_the_proxy_reads(
        self, console_api: Postgres
    ) -> None:
        """Match the golden file that the specs of the proxy check against its schema."""
        await seed(console_api)
        await console_api.connection.execute("UPDATE rbac.revision SET value = 7")

        config = await scalar(console_api, "SELECT console.config()")

        assert config == json.loads(PROXY_CONSOLE_CONFIG.read_text())

    async def test_is_the_same_for_every_token(self, console_api: Postgres) -> None:
        """Give the proxy every schema, whatever the claim of the token that the call carries."""
        await seed(console_api)
        await as_token(console_api, "dp_api", ["hr"], database_role=True)

        config = await scalar(console_api, "SELECT console.config()")

        assert isinstance(config, dict)
        assert sorted(config["schemas"]) == ["hr", "sales"]

    async def test_lists_a_schema_with_no_table_as_an_empty_list(
        self, console_api: Postgres
    ) -> None:
        """Say that the schema exists, so the proxy keeps its cache rules."""
        await add_schema(console_api, "sales", claim="cpf")

        config = await scalar(console_api, "SELECT console.config()")

        assert isinstance(config, dict)
        assert (
            json.dumps(config["schemas"], sort_keys=True) == '{"sales": {"tables": []}}'
        )


async def add_policy_schema(
    pg: Postgres, name: str, grants: dict[str, list[tuple[str, str]]], claim: str
) -> None:
    """Add a provisioned schema whose access policy has the given grants for each subject."""
    await add_schema(pg, name, claim=claim)
    await pg.connection.execute(
        SQL(
            "ALTER TABLE {}.access_policy ADD COLUMN unit_type text, ADD COLUMN unit_id text"
        ).format(Identifier(name))
    )
    for subject, units in grants.items():
        for unit_type, unit_id in units:
            await pg.connection.execute(
                SQL("INSERT INTO {}.access_policy VALUES (%s, %s, %s)").format(
                    Identifier(name)
                ),
                (subject, unit_type, unit_id),
            )


@pytest.mark.postgres
class TestProxyScopes:
    """console.scopes() names the grants of each subject, so the proxy shares its cache between equal grants."""

    async def test_subjects_with_the_same_grants_get_the_same_scope(
        self, console_api: Postgres
    ) -> None:
        """Compare the grants as a set: their order and their subject do not matter."""
        await add_policy_schema(
            console_api,
            "sales",
            {
                "alice": [("cras", "1"), ("cras", "2")],
                "bob": [("cras", "2"), ("cras", "1")],
                "carol": [("cras", "1")],
                "dave": [("*", "*")],
                "erin": [("*", "*")],
            },
            "preferred_username",
        )

        config = await scalar(console_api, "SELECT console.config()")

        assert isinstance(config, dict)
        scopes = config["scopes"]
        assert isinstance(scopes, dict)
        assert scopes["alice"] == scopes["bob"]
        assert scopes["dave"] == scopes["erin"]
        assert len({scopes["alice"], scopes["carol"], scopes["dave"]}) == 3
        assert all(len(str(scope)) == 32 for scope in scopes.values())

    async def test_the_grants_of_every_schema_count(
        self, console_api: Postgres
    ) -> None:
        """Give another scope to a subject that has the same grants in one schema and more in another."""
        await add_policy_schema(
            console_api,
            "sales",
            {"alice": [("cras", "1")], "bob": [("cras", "1")]},
            "preferred_username",
        )
        await add_policy_schema(
            console_api, "hr", {"bob": [("cras", "1")]}, "preferred_username"
        )

        config = await scalar(console_api, "SELECT console.config()")

        assert isinstance(config, dict)
        scopes = config["scopes"]
        assert isinstance(scopes, dict)
        assert scopes["alice"] != scopes["bob"]

    async def test_the_scope_changes_with_a_grant(self, console_api: Postgres) -> None:
        """Move the scope of a subject when it gets one more grant."""
        await add_policy_schema(
            console_api, "sales", {"alice": [("cras", "1")]}, "preferred_username"
        )
        before = await scalar(console_api, "SELECT console.scopes() -> 'alice'")

        await console_api.connection.execute(
            "INSERT INTO sales.access_policy VALUES ('alice', 'cras', '2')"
        )

        assert await scalar(console_api, "SELECT console.scopes() -> 'alice'") != before

    async def test_no_scope_when_a_schema_uses_another_claim(
        self, console_api: Postgres
    ) -> None:
        """The proxy keys the cache by the user name, so a schema with another claim must not share entries."""
        await add_policy_schema(
            console_api, "sales", {"alice": [("cras", "1")]}, "preferred_username"
        )
        await add_policy_schema(console_api, "hr", {"alice": [("cras", "1")]}, "cpf")

        config = await scalar(console_api, "SELECT console.config()")

        assert isinstance(config, dict)
        assert "scopes" not in config

    async def test_no_scope_for_a_schema_without_a_policy_table(
        self, console_api: Postgres
    ) -> None:
        """Leave the key out when no schema has a policy yet."""
        await add_schema(
            console_api, "sales", claim="preferred_username", provisioned=False
        )

        config = await scalar(console_api, "SELECT console.config()")

        assert isinstance(config, dict)
        assert "scopes" not in config


@pytest.mark.postgres
class TestWorkerContract:
    """The sync worker reads the configuration that the console tables describe, through its own model."""

    async def test_the_document_of_the_console_is_a_valid_sync_config(
        self, console_api: Postgres
    ) -> None:
        """Parse what read_sync_config returns, with every table of every schema."""
        await seed(console_api)

        config = SyncConfig.model_validate_json(
            await read_sync_config(console_api.backend)
        )

        assert {
            name: sorted(table.name for table in schema.tables)
            for name, schema in config.schemas.items()
        } == {"hr": ["p.d.staff"], "sales": ["p.d.events", "p.d.orders"]}

    async def test_a_partitioned_table_keeps_its_settings_through_the_database(
        self, console_api: Postgres
    ) -> None:
        """Carry n, the fallback, the partitioning and the sort from the row to the model."""
        await seed(console_api)

        config = SyncConfig.model_validate_json(
            await read_sync_config(console_api.backend)
        )

        [events] = [t for t in config.schemas["sales"].tables if t.name == "p.d.events"]
        assert isinstance(events, PartitionedTable)
        assert (events.n, events.fallback, events.ducklake.sort) == (
            4,
            True,
            ["dt", "id"],
        )
        partitioning = events.ducklake.partitioning
        assert partitioning is not None
        assert [partition.column for partition in partitioning] == ["dt"]
