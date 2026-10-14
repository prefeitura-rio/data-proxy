from collections.abc import AsyncGenerator

import pytest
from httpx2 import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from console.database import create_engine
from console.dependencies import get_catalog
from console.main import app
from console.settings import settings
from helpers import document, table

SCHEMA = "test"
ROLE = f"policy_writer_{SCHEMA}"
AUTHENTICATOR = "authenticator"


@pytest.fixture
async def objects(engine: AsyncEngine) -> AsyncGenerator[AsyncEngine]:
    """Start without the objects that the status looks for, and remove them after."""

    async def clean() -> None:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
            await connection.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
            await connection.execute(text(f"DROP ROLE IF EXISTS {AUTHENTICATOR}"))

    await clean()
    yield engine
    await clean()


@pytest.fixture
async def catalog() -> AsyncGenerator[AsyncEngine]:
    """Give the console a catalog database that has no metadata schema yet."""
    admin = create_engine(settings.PG_DATABASE_URL)
    async with admin.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text("DROP DATABASE IF EXISTS console_catalog_test"))
        await connection.execute(text("CREATE DATABASE console_catalog_test"))
    url = settings.PG_DATABASE_URL.rsplit("/", 1)[0] + "/console_catalog_test"
    engine = create_engine(url)
    app.dependency_overrides[get_catalog] = lambda: engine
    yield engine
    app.dependency_overrides.pop(get_catalog, None)
    await engine.dispose()
    async with admin.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(text("DROP DATABASE IF EXISTS console_catalog_test"))
    await admin.dispose()


async def configure(api: AsyncClient, tables: list[str]) -> None:
    """Store a config whose schema has these tables."""
    body = document({name: table(f"proj.ds.{name}") for name in tables})
    stored = await api.put("/config", json=body, headers={"If-Match": '"0"'})
    assert stored.status_code == 200


async def run(engine: AsyncEngine, *statements: str) -> None:
    """Run SQL statements in one transaction."""
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))


def verdicts(body: dict[str, object]) -> dict[str, bool]:
    """Read the checks of a status as a map from name to result."""
    checks = body["checks"]
    assert isinstance(checks, list)
    return {item["name"]: item["ok"] for item in checks}  # pyright: ignore[reportUnknownVariableType]


@pytest.mark.postgres
class TestSchemaStatus:
    """Report what exists for a schema, without changing anything."""

    @pytest.mark.usefixtures("objects")
    async def test_answers_404_for_a_schema_that_the_config_doesnt_know(
        self, api: AsyncClient
    ) -> None:
        """Refuse to report on a schema that isn't configured."""
        response = await api.get("/schemas/missing/status")

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")

    @pytest.mark.usefixtures("objects", "catalog")
    async def test_reports_every_missing_object_of_a_new_schema(
        self, api: AsyncClient
    ) -> None:
        """Show that nothing exists yet, with a reason for each check."""
        await configure(api, ["orders"])

        response = await api.get(f"/schemas/{SCHEMA}/status")

        assert response.status_code == 200
        body = response.json()
        assert body["schema"] == SCHEMA
        assert body["ready"] is False
        assert verdicts(body) == {
            "postgres_schema": False,
            "access_policy": False,
            "policy_writer": False,
            "views": False,
            "catalog": False,
        }
        assert all(item["detail"] for item in body["checks"])

    async def test_reports_a_schema_that_is_ready(
        self, api: AsyncClient, objects: AsyncEngine, catalog: AsyncEngine
    ) -> None:
        """Say ready when the schema, its policy objects, its views and its catalog exist."""
        await configure(api, ["orders"])
        await run(
            objects,
            f'CREATE SCHEMA "{SCHEMA}"',
            f'CREATE TABLE "{SCHEMA}".access_policy (subject text)',
            f'CREATE TABLE "{SCHEMA}".access_log (subject text)',
            f'CREATE VIEW "{SCHEMA}".orders AS SELECT 1 AS id',
            f"CREATE ROLE {AUTHENTICATOR}",
            f"CREATE ROLE {ROLE}",
            f"GRANT {ROLE} TO {AUTHENTICATOR}",
        )
        await run(catalog, f'CREATE SCHEMA "{SCHEMA}"')

        response = await api.get(f"/schemas/{SCHEMA}/status")

        body = response.json()
        assert verdicts(body) == {
            "postgres_schema": True,
            "access_policy": True,
            "policy_writer": True,
            "views": True,
            "catalog": True,
        }
        assert body["ready"] is True

    @pytest.mark.usefixtures("catalog")
    async def test_names_the_tables_that_have_no_view(
        self, api: AsyncClient, objects: AsyncEngine
    ) -> None:
        """List the configured tables that the schema doesn't serve yet."""
        await configure(api, ["orders", "items"])
        await run(
            objects,
            f'CREATE SCHEMA "{SCHEMA}"',
            f'CREATE VIEW "{SCHEMA}".orders AS SELECT 1 AS id',
        )

        body = (await api.get(f"/schemas/{SCHEMA}/status")).json()

        views: dict[str, object] = next(
            item for item in body["checks"] if item["name"] == "views"
        )
        assert views["ok"] is False
        assert views["detail"] == "Missing: items"

    async def test_reports_a_policy_writer_that_the_authenticator_can_not_use(
        self, api: AsyncClient, objects: AsyncEngine
    ) -> None:
        """Say that the role exists but PostgREST can't switch to it."""
        await configure(api, ["orders"])
        await run(objects, f"CREATE ROLE {AUTHENTICATOR}", f"CREATE ROLE {ROLE}")

        body = (await api.get(f"/schemas/{SCHEMA}/status")).json()

        writer: dict[str, str] = next(
            item for item in body["checks"] if item["name"] == "policy_writer"
        )
        assert writer["ok"] is False
        assert AUTHENTICATOR in writer["detail"]

    @pytest.mark.usefixtures("objects")
    async def test_leaves_the_catalog_out_when_the_console_has_no_catalog_database(
        self, api: AsyncClient
    ) -> None:
        """Report only what the console can reach."""
        await configure(api, ["orders"])

        body = (await api.get(f"/schemas/{SCHEMA}/status")).json()

        assert "catalog" not in verdicts(body)
