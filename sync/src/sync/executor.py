"""Render SQL templates and execute statements or typed queries."""

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import TypeAdapter

from .templates import render_template
from .types import DatabaseConnection, TemplateValue


@dataclass(frozen=True, slots=True)
class Executor[Params, Rows]:
    """Render SQL templates through one typed database connection."""

    conn: DatabaseConnection[Params, Rows]

    async def execute(
        self,
        path: str,
        mapping: Mapping[str, TemplateValue] | None = None,
        *,
        params: Params | None = None,
    ) -> None:
        """Render and execute one SQL statement."""
        sql = render_template(path, mapping or {})
        await self.conn.execute(sql, params=params)

    async def query[Expected](
        self,
        path: str,
        mapping: Mapping[str, TemplateValue] | None = None,
        *,
        params: Params | None = None,
        expect: type[Expected],
    ) -> list[Expected]:
        """Render, run, and validate one SQL query."""
        sql = render_template(path, mapping or {})
        rows = await self.conn.query(sql, params=params)
        return TypeAdapter(list[Expected]).validate_python(rows)
