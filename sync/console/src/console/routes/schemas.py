"""Routes that read and write the schemas of the sync config, and their tables."""

from fastapi import APIRouter, Response

from console.config import is_object
from console.editor import Edit, Guard
from console.models import ConfigCheck, SchemaSummary

router = APIRouter(prefix="/schemas", tags=["schemas"])


@router.get("")
async def list_schemas(edit: Edit) -> list[SchemaSummary]:
    """List the schemas by name, each with the names of its tables."""
    document = await edit.document()
    return [
        SchemaSummary(name=name, tables=sorted(schema.tables))
        for name, schema in sorted(document.schemas.items())
    ]


@router.get("/{schema}")
async def get_schema(schema: str, edit: Edit) -> dict[str, object]:
    """Show one schema with its tables. The ETag names its content."""
    return await edit.show(("schemas", schema))


@router.put("/{schema}")
async def put_schema(
    schema: str, body: dict[str, object], edit: Edit, guard: Guard
) -> dict[str, object] | ConfigCheck:
    """Create a schema with If-None-Match: *, or replace it with If-Match and its ETag.

    Add ?dry_run=true to check the change and list what it adds, removes and changes.
    """
    return await edit.replace(("schemas", schema), body, guard)


@router.patch("/{schema}")
async def patch_schema(
    schema: str, body: dict[str, object], edit: Edit, guard: Guard
) -> dict[str, object] | ConfigCheck:
    """Change part of a schema with a JSON Merge Patch. A null value removes a key."""
    return await edit.patch(("schemas", schema), body, guard)


@router.delete("/{schema}", status_code=204)
async def delete_schema(schema: str, edit: Edit, guard: Guard) -> Response:
    """Remove a schema and its tables from the config. The synced data stays."""
    return await edit.delete(("schemas", schema), guard)


@router.get("/{schema}/tables")
async def list_tables(schema: str, edit: Edit) -> dict[str, object]:
    """Show the tables of a schema as a map by table name."""
    tables = (await edit.read(("schemas", schema))).get("tables")
    return tables if is_object(tables) else {}


@router.get("/{schema}/tables/{table}")
async def get_table(schema: str, table: str, edit: Edit) -> dict[str, object]:
    """Show one table. The ETag names its content."""
    return await edit.show(("schemas", schema, "tables", table))


@router.put("/{schema}/tables/{table}")
async def put_table(
    schema: str, table: str, body: dict[str, object], edit: Edit, guard: Guard
) -> dict[str, object] | ConfigCheck:
    """Create a table with If-None-Match: *, or replace it with If-Match and its ETag.

    The table name must be the last part of `source_table`.
    """
    return await edit.replace(("schemas", schema, "tables", table), body, guard)


@router.patch("/{schema}/tables/{table}")
async def patch_table(
    schema: str, table: str, body: dict[str, object], edit: Edit, guard: Guard
) -> dict[str, object] | ConfigCheck:
    """Change part of a table with a JSON Merge Patch. A null value removes a key."""
    return await edit.patch(("schemas", schema, "tables", table), body, guard)


@router.delete("/{schema}/tables/{table}", status_code=204)
async def delete_table(schema: str, table: str, edit: Edit, guard: Guard) -> Response:
    """Remove a table from the config. Its data stays, and the next sync drops its view."""
    return await edit.delete(("schemas", schema, "tables", table), guard)
