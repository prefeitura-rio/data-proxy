"""Install the rbac rules and the roles, and read them back."""

import itertools
import tomllib
from collections.abc import Mapping
from typing import Final

import psycopg
from psycopg.rows import TupleRow
from psycopg.sql import SQL, Identifier, Literal
from pydantic import BaseModel, Field

from sync.types import (
    TemplateValue,
)
from tests.constants import (
    RBAC_RULES,
    REQUEST_ROLES,
    SIGNER,
)
from tests.fixtures.types import Postgres
from tests.helpers.core import helm_sql, scalar


class RbacRole(BaseModel):
    """One role of the rules file."""

    name: str
    description: str
    inherits: list[str] = Field(default_factory=list)
    permissions: list[str]


class RbacRules(BaseModel):
    """The rules file that the create_rbac template turns into SQL."""

    permissions: dict[str, dict[str, str]]
    roles: list[RbacRole]

    @property
    def features(self) -> list[str]:
        """Return every permission name, the action and the object joined by an underscore."""
        return sorted(
            f"{action}_{subject}"
            for subject, actions in self.permissions.items()
            for action in actions
            if action != "description"
        )

    def effective(self, role: str) -> set[str]:
        """Return the permissions of a role and of every role that it inherits."""
        by_name = {candidate.name: candidate for candidate in self.roles}
        pending, seen, found = [role], set[str](), set[str]()
        while len(pending) > 0:
            name = pending.pop()
            if name in seen or name not in by_name:
                continue
            seen.add(name)
            found.update(by_name[name].permissions)
            pending.extend(by_name[name].inherits)

        return found

    def changed(self, role: str, **update: list[str]) -> RbacRules:
        """Return a copy in which one role has other values."""
        return self.model_copy(
            update={
                "roles": [
                    candidate.model_copy(update=update)
                    if candidate.name == role
                    else candidate
                    for candidate in self.roles
                ]
            }
        )

    def context(self) -> Mapping[str, TemplateValue]:
        """Return the rules as the template context."""
        return {
            "permissions": {
                subject: dict(actions) for subject, actions in self.permissions.items()
            },
            "roles": [
                {
                    "name": role.name,
                    "description": role.description,
                    "inherits": list(role.inherits),
                    "permissions": list(role.permissions),
                }
                for role in self.roles
            ],
        }


RBAC: Final = RbacRules.model_validate(tomllib.loads(RBAC_RULES.read_text()))


SOURCE_HASH: Final = "0f" * 32


async def install_rbac(
    pg: Postgres, rules: RbacRules = RBAC, source_hash: str = SOURCE_HASH
) -> None:
    """Render create_rbac with a rules file and run it in the test transaction."""
    await pg.connection.execute(
        helm_sql(
            "create_rbac",
            {
                "rules": rules.context(),
                "anonymous_role": Identifier("anon"),
                "source_hash": source_hash,
            },
        ).encode()
    )


async def stored_roles(pg: Postgres) -> list[tuple[str, list[str]]]:
    """Return the rows of rbac.roles."""
    cursor = await pg.connection.execute(
        b"SELECT name, permissions FROM rbac.roles ORDER BY name"
    )
    return [(name, permissions) for name, permissions in await cursor.fetchall()]


async def install_console_triggers(pg: Postgres) -> None:
    """Create the console tables that the triggers watch, and install the triggers, in the test transaction."""
    await pg.connection.execute(
        b"""
        CREATE SCHEMA console;
        CREATE TABLE console.schemas (name text);
        CREATE TABLE console.tables (name text);
        CREATE TABLE console.rls (id integer)
        """
    )
    await pg.connection.execute(helm_sql("create_console_triggers").encode())


async def queued_workflows(pg: Postgres, dbos_schema: str) -> list[str]:
    """Return the names of the workflows that wait in the DBOS queues."""
    cursor = await pg.connection.execute(
        SQL(
            """
            SELECT name FROM {}.workflow_status
            WHERE status = 'ENQUEUED' AND name IN ('reload_proxy', 'provision_schemas', 'cleanup_tables', 'reconcile_schedules')
            """
        ).format(Identifier(dbos_schema))
    )
    return sorted(itertools.starmap(str, await cursor.fetchall()))


async def revision(pg: Postgres) -> int:
    """Return the revision of the rules and of the access policy."""
    value = await scalar(pg, "SELECT value FROM rbac.revision")
    assert isinstance(value, int)
    return value


SERVING: Final = "auth_v"


async def make_serving_login(pg: Postgres) -> None:
    """Create a serving login that is a member of every role of the rules and of the anonymous role."""
    await pg.connection.execute(
        SQL("CREATE ROLE {} LOGIN NOINHERIT").format(Identifier(SERVING))
    )
    for role in (*(role.name for role in RBAC.roles), "anon"):
        await pg.connection.execute(
            SQL("GRANT {} TO {}").format(Identifier(role), Identifier(SERVING))
        )


async def verify_rbac(pg: Postgres, extra_roles: tuple[str, ...] = ()) -> None:
    """Render verify_rbac for the roles of the rules, and run it in the test transaction."""
    await pg.connection.execute(
        helm_sql(
            "verify_rbac",
            {
                "roles": SQL(", ").join(
                    Literal(role)
                    for role in (*(role.name for role in RBAC.roles), *extra_roles)
                ),
                "anonymous_role": Literal("anon"),
                "serving_role": Literal(SERVING),
            },
        ).encode()
    )


async def create_request_roles(pg: Postgres, password: str) -> None:
    """Render create_roles for test role names and run it in the test transaction."""
    await pg.connection.execute(
        helm_sql(
            "create_roles",
            {
                "roles": SQL(", ").join(Literal(role) for role in REQUEST_ROLES),
                "baseline_role": Literal("dp_t_api"),
                "anonymous_role": Literal("anon_t"),
                "serving_role": Literal("auth_t"),
                "serving_password": Literal(password),
                "signer_role": Literal(SIGNER),
                "signer_password": Literal(password),
            },
        ).encode()
    )


async def create_schema_reader(
    pg: Postgres, name: str, template: str = "create_schema_reader"
) -> None:
    """Render one reader template for a schema of the test catalog and run it in the test transaction."""
    await pg.connection.execute(
        helm_sql(
            template,
            {
                "name": Literal(name),
                "signer": Literal(SIGNER),
                "writer": Literal("test"),
            },
        ).encode()
    )


def install_rbac_sync(
    connection: psycopg.Connection[TupleRow], rules: RbacRules
) -> None:
    """Install a rules file in the open transaction of a property test."""
    connection.execute(
        helm_sql(
            "create_rbac",
            {
                "rules": rules.context(),
                "anonymous_role": Identifier("anon"),
                "source_hash": SOURCE_HASH,
            },
        ).encode()
    )


def stored_roles_sync(
    connection: psycopg.Connection[TupleRow],
) -> list[tuple[str, list[str]]]:
    """Return the rows of rbac.roles."""
    rows = connection.execute(
        b"SELECT name, permissions FROM rbac.roles ORDER BY name"
    ).fetchall()

    return [(str(name), list(permissions)) for name, permissions in rows]
