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
