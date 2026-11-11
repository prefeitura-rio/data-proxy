"""Build the console documents, the runs and the schedules."""

from psycopg.types.json import Jsonb
from pydantic import JsonValue

from tests.fixtures.types import Postgres
from tests.helpers.core import scalar


async def set_status(pg: Postgres, run: str, status: str) -> None:
    """Move a run to a status, as the workers do."""
    await pg.connection.execute(
        "UPDATE dbos.workflow_status SET status = %s WHERE workflow_uuid = %s",
        (status, run),
    )


async def add_schedule(pg: Postgres, name: str) -> None:
    """Add a schedule as DBOS stores it."""
    await pg.connection.execute(
        "INSERT INTO dbos.workflow_schedules (schedule_id, schedule_name, workflow_name, schedule, context) "
        "VALUES (%s, %s, 'run_sync', '0 2 * * *', '{}')",
        (f"id-{name}", name),
    )


Document = dict[str, JsonValue]


async def export(pg: Postgres) -> Document:
    """Return the configuration that the console exports."""
    document = await scalar(pg, "SELECT console.sync_config()")
    assert isinstance(document, dict)

    return document


async def export_config(pg: Postgres) -> Document:
    """Return the configuration that the console exports by project."""
    document = await scalar(pg, "SELECT console.export_config()")
    assert isinstance(document, dict)

    return document


async def preview(pg: Postgres, document: Document) -> list[tuple[str, str, str, str]]:
    """Return what an import of the document would do, as (object, schema, key, action)."""
    cursor = await pg.connection.execute(
        b"SELECT object, schema_name, key, action FROM console.import_preview(%s)",
        (Jsonb(document),),
    )

    return [(a, b, c, d) for a, b, c, d in await cursor.fetchall()]


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


async def role_setting(pg: Postgres, role: str) -> str | None:
    """Return the value of the PostgREST setting that a role carries, or None."""
    cursor = await pg.connection.execute(
        b"""
        SELECT s.setting
        FROM pg_db_role_setting AS d
        INNER JOIN pg_roles AS r ON d.setrole = r.oid
        CROSS JOIN LATERAL unnest(d.setconfig) AS s (setting)
        WHERE r.rolname = %s AND s.setting LIKE 'pgrst.db_schemas=%%'
        """,
        (role,),
    )
    row = await cursor.fetchone()

    return None if row is None else str(row[0])


def member(project: str, short_name: str) -> Document:
    """Build the export of one schema of a project, with one table that carries the names of both."""
    table: Document = {"name": f"p.d.{project}_{short_name}", "strategy": "full"}

    return {"claim": "cpf", "source": {"type": "bigquery"}, "tables": [table]}


def projects_document(*schemas: tuple[str, str]) -> Document:
    """Build a document of the projects form from (project, short name) pairs, with an empty project `empty`."""
    projects: dict[str, dict[str, JsonValue]] = {"empty": {}}
    for project, short_name in schemas:
        projects.setdefault(project, {})[short_name] = {
            "claim": "cpf",
            "tables": member(project, short_name)["tables"],
        }

    return {
        "projects": {name: {"schemas": members} for name, members in projects.items()}
    }


ADD_PROJECT = "INSERT INTO console.projects (name) VALUES (%s)"
ADD_SCHEMA = "INSERT INTO console.schemas (project, short_name) VALUES (%s, %s)"


async def add_projects(pg: Postgres, *projects: str) -> None:
    """Add projects to the console."""
    for project in projects:
        await pg.connection.execute(ADD_PROJECT.encode(), (project,))


async def add_schemas(pg: Postgres, *schemas: tuple[str, str]) -> None:
    """Add schemas to the console, each as a project and a short name."""
    for project, short_name in schemas:
        await pg.connection.execute(ADD_SCHEMA.encode(), (project, short_name))


async def load_claims(
    pg: Postgres, *, schemas: str = "", projects: str = "", role: str = "dp_admin"
) -> str:
    """Give the connection the claims of a token, run `rbac.load()` as the pre-request function does, and return the schemas that it sets."""
    await pg.connection.execute(
        """
        SELECT
            set_config('app.claim_role', %s, true),
            set_config('app.claim_schemas', %s, true),
            set_config('app.claim_projects', %s, true),
            set_config('request.jwt.claims', '{"preferred_username": "ana"}', true)
        """,
        (role, schemas, projects),
    )
    await pg.connection.execute(b"SELECT rbac.load()")

    return str(await scalar(pg, "SELECT current_setting('rbac.schemas')"))
