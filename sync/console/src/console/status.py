"""Check which objects of a schema exist, without changing anything."""

from typing import TYPE_CHECKING

from pydantic import BaseModel
from sqlalchemy import text

from console.models import Check, SchemaStatus
from console.settings import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

SCHEMA_EXISTS = (
    "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :name) AS found"
)
ROLE_EXISTS = "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :name) AS found"
ROLE_MEMBER = """
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :member)
    AND pg_has_role(:member, :role, 'MEMBER') AS found
"""
POLICY_TABLES = """
SELECT to_regclass(format('%I.access_policy', CAST(:name AS text))) IS NOT NULL
    AND to_regclass(format('%I.access_log', CAST(:name AS text))) IS NOT NULL AS found
"""
VIEW_NAMES = "SELECT viewname FROM pg_views WHERE schemaname = :schema"


class Found(BaseModel):
    """The one boolean that a query answers."""

    found: bool


async def exists(engine: AsyncEngine, query: str, **values: str) -> bool:
    """Run a query that answers one boolean named found."""
    async with engine.connect() as connection:
        result = await connection.execute(text(query), values)
        return Found.model_validate(dict(result.mappings().one())).found


async def policy_writer(engine: AsyncEngine, role: str) -> Check:
    """Check the policy writer role of a schema, and that the authenticator role can use it."""
    authenticator = settings.AUTH_AUTHENTICATOR_ROLE
    if not await exists(engine, ROLE_EXISTS, name=role):
        return Check(
            name="policy_writer", ok=False, detail=f"The role {role} doesn't exist"
        )
    usable = await exists(engine, ROLE_MEMBER, member=authenticator, role=role)
    detail = (
        f"The role {role} exists"
        if usable
        else f"{authenticator} isn't a member of {role}"
    )
    return Check(name="policy_writer", ok=usable, detail=detail)


async def missing_views(engine: AsyncEngine, schema: str, tables: list[str]) -> Check:
    """Check that every configured table has a view in the schema."""
    async with engine.connect() as connection:
        views = set(
            (await connection.execute(text(VIEW_NAMES), {"schema": schema})).scalars()
        )
    missing = sorted(set(tables) - views)
    detail = "Missing: " + ", ".join(missing) if missing else "Every table has a view"
    return Check(name="views", ok=not missing, detail=detail)


async def schema_status(
    engine: AsyncEngine, catalog: AsyncEngine | None, schema: str, tables: list[str]
) -> SchemaStatus:
    """Report what exists for a schema in the application database and, when known, the catalog."""
    present = await exists(engine, SCHEMA_EXISTS, name=schema)
    policy = await exists(engine, POLICY_TABLES, name=schema)
    checks = [
        Check(
            name="postgres_schema",
            ok=present,
            detail="The schema exists"
            if present
            else f"The schema {schema} doesn't exist",
        ),
        Check(
            name="access_policy",
            ok=policy,
            detail="access_policy and access_log exist"
            if policy
            else "access_policy or access_log is missing",
        ),
        await policy_writer(engine, f"policy_writer_{schema}"),
        await missing_views(engine, schema, tables),
    ]
    if catalog is not None:
        found = await exists(catalog, SCHEMA_EXISTS, name=schema)
        checks.append(
            Check(
                name="catalog",
                ok=found,
                detail="The catalog has a metadata schema"
                if found
                else f"The catalog has no metadata schema {schema}",
            )
        )
    return SchemaStatus(
        schema_name=schema, ready=all(check.ok for check in checks), checks=checks
    )
