"""Substitute mapping into a cached SQL template and return the final SQL."""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import LiteralString, cast

from psycopg.sql import SQL, Composable

SQL_DIR = Path(__file__).parent / "sql"


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """Template path and substitution values for one SQL statement."""

    path: str
    mapping: Mapping[str, str | Composable]


@lru_cache
def read_template(name: str, root: Path) -> str:
    """Read and cache a SQL template by name from one SQL directory."""
    return (root / f"{name}.sql").read_text()


def load_template(spec: TemplateSpec, root: Path) -> str:
    """Substitute a mapping into its named SQL template.

    Values that are `Composable` (`Identifier`, `Literal`, `SQL`) render
    through psycopg's own quoting and escaping. Plain strings pass through
    unescaped and must already be safe (for example a validated raw keyword
    like "true" or "false").
    """
    rendered: dict[str, str] = {}

    for key, value in spec.mapping.items():
        match value:
            case Composable():
                rendered[key] = value.as_string(None)
            case _:
                rendered[key] = value

    return Template(read_template(spec.path, root)).substitute(rendered)


def render_template(
    path: str,
    mapping: Mapping[str, str | Composable],
    *,
    root: Path = SQL_DIR,
) -> LiteralString:
    """Load and substitute a SQL template in one call.

    The result is safe by construction: every substituted value is either a
    `Composable` that psycopg escapes, or a plain string that the caller has
    already validated.
    """
    return cast(
        "LiteralString",
        load_template(TemplateSpec(path=path, mapping=mapping), root),
    )


def render_fragment(
    path: str,
    mapping: Mapping[str, str | Composable],
) -> Composable:
    """Render one composable SQL fragment for use in another template."""
    return SQL(render_template(path, mapping))
