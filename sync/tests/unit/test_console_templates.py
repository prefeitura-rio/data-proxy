import pytest
from psycopg.sql import Identifier, Literal

from tests.fixtures.console import add_schema, refusal
from tests.fixtures.types import Postgres
from tests.helpers import helm_sql, role_setting, scalar

INSUFFICIENT = "42501"


@pytest.mark.postgres
class TestGrantProxyConfig:
    """grant_proxy_config lets the anonymous role read the one document that the proxy needs."""

    async def test_the_anonymous_role_reads_the_config_and_nothing_else_of_the_console(
        self, console_api: Postgres
    ) -> None:
        """Keep the console views and the other functions closed to a request with no token."""
        await add_schema(console_api, "sales", claim="cpf")
        await console_api.connection.execute(
            helm_sql(
                "grant_proxy_config", {"anonymous_role": Identifier("anon")}
            ).encode()
        )
        await console_api.connection.execute(b"SET LOCAL ROLE anon")

        config = await scalar(console_api, "SELECT console.config()")

        assert isinstance(config, dict)
        assert list(config["schemas"]) == ["sales"]
        assert (
            await refusal(console_api, "SELECT console.sync_config()") == INSUFFICIENT
        )
        assert await refusal(console_api, "SELECT * FROM console.runs") == INSUFFICIENT
        assert (
            await refusal(console_api, "SELECT * FROM console.schemas") == INSUFFICIENT
        )

    async def test_without_the_grant_the_anonymous_role_cannot_read_the_config(
        self, console_api: Postgres
    ) -> None:
        """Show that the grant is what opens the document."""
        await console_api.connection.execute(b"SET LOCAL ROLE anon")

        assert await refusal(console_api, "SELECT console.config()") == INSUFFICIENT


@pytest.mark.postgres
class TestDbSchemasSetting:
    """set_db_schemas and reset_db_schemas keep the list of schemas that PostgREST serves."""

    async def test_sets_the_schemas_as_a_setting_of_the_serving_role_and_resets_them(
        self, postgres: Postgres
    ) -> None:
        """Store the comma-separated list, and remove it so that the environment applies again."""
        await postgres.connection.execute(b"CREATE ROLE serving_t NOLOGIN")
        assert await role_setting(postgres, "serving_t") is None

        await postgres.connection.execute(
            helm_sql(
                "set_db_schemas",
                {
                    "serving_role": Identifier("serving_t"),
                    "db_schemas": Literal("a,b,console"),
                },
            ).encode()
        )

        assert (
            await role_setting(postgres, "serving_t") == "pgrst.db_schemas=a,b,console"
        )

        await postgres.connection.execute(
            helm_sql(
                "reset_db_schemas", {"serving_role": Identifier("serving_t")}
            ).encode()
        )

        assert await role_setting(postgres, "serving_t") is None

    async def test_a_second_set_replaces_the_first(self, postgres: Postgres) -> None:
        """Keep one value, the last one."""
        await postgres.connection.execute(b"CREATE ROLE serving_t NOLOGIN")

        for schemas in ("a,console", "a,b,console"):
            await postgres.connection.execute(
                helm_sql(
                    "set_db_schemas",
                    {
                        "serving_role": Identifier("serving_t"),
                        "db_schemas": Literal(schemas),
                    },
                ).encode()
            )

        assert (
            await role_setting(postgres, "serving_t") == "pgrst.db_schemas=a,b,console"
        )
