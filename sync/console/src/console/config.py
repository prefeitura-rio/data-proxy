"""The config document: its two forms, its validation, and how a write changes it."""

import re
from typing import TYPE_CHECKING, ClassVar, TypeIs

from engine.models import SyncConfig
from pydantic import BaseModel, ConfigDict, ValidationError

from console import revisions
from console.models import Change, Config, ConfigCheck, ErrorItem, RevisionDetail
from console.problems import Problem

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncEngine

    from console.dependencies import Write

SCHEMA_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
TABLE_TAGS = frozenset({"full", "partitioned"})


class ApiTable(BaseModel):
    """A table in the form that the API shows. The other fields pass through."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    source_table: str


class ApiSchema(BaseModel):
    """A schema in the form that the API shows. The other fields pass through."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    tables: dict[str, ApiTable] = {}


class ApiDocument(BaseModel):
    """The config in the form that the API shows: tables are a map by table name."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    schemas: dict[str, ApiSchema] = {}


class StoredTable(BaseModel):
    """A table in the form that the database stores, like sync.json."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    name: str


class StoredSchema(BaseModel):
    """A schema in the form that the database stores."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    tables: list[StoredTable] = []


class StoredDocument(BaseModel):
    """The config in the form that the database stores."""

    schemas: dict[str, StoredSchema] = {}


def is_object(value: object) -> TypeIs[dict[str, object]]:
    """Tell the type checker that a JSON value is an object."""
    return isinstance(value, dict)


def to_api(stored: dict[str, object]) -> dict[str, object]:
    """Show a stored document with the tables of each schema as a map by table name."""
    document = StoredDocument.model_validate(stored)
    schemas: dict[str, object] = {}
    for name, schema in document.schemas.items():
        tables: dict[str, object] = {
            table.name.split(".")[-1]: {
                "source_table": table.name,
                **table.model_dump(exclude={"name"}),
            }
            for table in schema.tables
        }
        schemas[name] = {**schema.model_dump(exclude={"tables"}), "tables": tables}
    return {"schemas": schemas}


def pointer_of(
    location: tuple[int | str, ...], schema: str, keys: list[str], kind: str
) -> str:
    """Turn the location of an engine error into a JSON Pointer in the API form."""
    match location:
        case ():
            return f"/schemas/{schema}"
        case ("schemas", str() as name, "tables", int(index), *rest):
            tail = [str(part) for part in rest]
            if tail and tail[0] in TABLE_TAGS:
                tail = tail[1:]
            if tail[:1] == ["name"]:
                tail[0] = "source_table"
            if kind == "union_tag_invalid":
                tail.append("strategy")
            return "/".join(["", "schemas", name, "tables", keys[index], *tail])
        case _:
            return "/" + "/".join(str(part) for part in location)


def stored_schema(schema: ApiSchema) -> dict[str, object]:
    """Turn one API schema into the stored form: a list of tables, each named by its source."""
    return {
        **schema.model_dump(exclude={"tables"}),
        "tables": [
            {"name": table.source_table, **table.model_dump(exclude={"source_table"})}
            for table in schema.tables.values()
        ],
    }


def key_errors(name: str, schema: ApiSchema) -> list[ErrorItem]:
    """Find the tables whose key is not the table name of their source."""
    return [
        ErrorItem(
            pointer=f"/schemas/{name}/tables/{key}",
            message=f"The key must be the table name of source_table: {table.source_table.split('.')[-1]}",
        )
        for key, table in schema.tables.items()
        if table.source_table.split(".")[-1] != key
    ]


def engine_errors(
    candidate: dict[str, object], name: str, keys: list[str]
) -> list[ErrorItem]:
    """Let the engine validate one schema, and place each error in the API form."""
    try:
        SyncConfig.model_validate(candidate)
    except ValidationError as error:
        return [
            ErrorItem(
                pointer=pointer_of(issue["loc"], name, keys, issue["type"]),
                message=issue["msg"],
            )
            for issue in error.errors()
        ]
    return []


def schema_errors(
    name: str, schema: ApiSchema, stored: dict[str, object]
) -> list[ErrorItem]:
    """Find every problem of one schema."""
    errors = key_errors(name, schema)
    if not SCHEMA_NAME.match(name):
        errors.append(
            ErrorItem(
                pointer=f"/schemas/{name}",
                message="A schema name is lowercase letters, digits and underscores, and starts with a letter.",
            )
        )
    return errors + engine_errors(
        {"schemas": {name: stored}}, name, list(schema.tables)
    )


def cross_schema_errors(schemas: dict[str, object]) -> list[ErrorItem]:
    """Let the engine validate the whole config, for the rules that cross schemas."""
    try:
        SyncConfig.model_validate({"schemas": schemas})
    except ValidationError as error:
        return [
            ErrorItem(pointer="/schemas", message=issue["msg"])
            for issue in error.errors()
        ]
    return []


def validate(api: dict[str, object]) -> tuple[dict[str, object], list[ErrorItem]]:
    """Check an API document and return the stored form and the problems.

    The engine validates each schema alone, so that an error has a place, and
    then the whole config, for the rules that cross schemas.
    """
    try:
        document = ApiDocument.model_validate(api)
    except ValidationError as error:
        return {}, [
            ErrorItem(
                pointer="/" + "/".join(str(part) for part in issue["loc"]),
                message=issue["msg"],
            )
            for issue in error.errors()
        ]

    schemas: dict[str, object] = {
        name: stored_schema(schema) for name, schema in document.schemas.items()
    }
    errors = [
        found
        for name, schema in document.schemas.items()
        for found in schema_errors(name, schema, stored_schema(schema))
    ]
    return {"schemas": schemas}, errors or cross_schema_errors(schemas)


def merge_patch(
    target: dict[str, object], patch: dict[str, object]
) -> dict[str, object]:
    """Apply a JSON Merge Patch (RFC 7396): objects merge, null removes, the rest replaces."""
    result = dict(target)
    for key, value in patch.items():
        current = result.get(key)
        if value is None:
            result.pop(key, None)
        elif is_object(value) and is_object(current):
            result[key] = merge_patch(current, value)
        else:
            result[key] = value
    return result


def changes(
    before: dict[str, object], after: dict[str, object], pointer: str = ""
) -> list[Change]:
    """List the parts of the document that were added, removed or changed."""
    found: list[Change] = []
    for key in sorted(before.keys() | after.keys()):
        path = f"{pointer}/{key}"
        old = before.get(key)
        new = after.get(key)
        if key not in before:
            found.append(Change(pointer=path, change="added"))
        elif key not in after:
            found.append(Change(pointer=path, change="removed"))
        elif is_object(old) and is_object(new):
            found.extend(changes(old, new, path))
        elif old != new:
            found.append(Change(pointer=path, change="changed"))
    return found


def config_of(revision: RevisionDetail | None) -> Config:
    """Build the API answer for a revision. No revision means an empty config."""
    if revision is None:
        return Config(revision=None, document={"schemas": {}})
    return Config(revision=revision, document=to_api(revision.document))


def etag_of(config: Config) -> str:
    """Return the ETag of a config: its revision id, or 0 before the first write."""
    return f'"{config.revision.id if config.revision else 0}"'


async def change(
    engine: AsyncEngine,
    write: Write,
    edit: Callable[[dict[str, object]], dict[str, object]],
) -> Config | ConfigCheck:
    """Edit the active config in one transaction, or only check the edit.

    The transaction locks the active revision and refuses when the caller read
    an older one. It validates the whole result before it writes anything.
    """
    async with engine.begin() as connection:
        current = await revisions.lock_active(connection, write.expected)
        before = config_of(current).document
        after = edit(before)
        stored, errors = validate(after)
        if write.dry_run:
            return ConfigCheck(
                valid=not errors, errors=errors, changes=changes(before, after)
            )
        if errors:
            raise Problem(422, "The config is not valid", errors)
        saved = await revisions.save(connection, stored, write.author, write.comment)
        return config_of(saved)
