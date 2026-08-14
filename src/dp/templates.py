"""Substitute mapping into a cached SQL template and return the final SQL."""

from functools import lru_cache
from pathlib import Path
from string import Template
from typing import TypedDict

from psycopg import sql

SQL_DIR = Path(__file__).parent / "sql"


class TemplateSpec(TypedDict):
    """Template path and substitution values for one SQL statement."""

    path: str
    mapping: dict[str, str | sql.Composable]


@lru_cache
def read_template(name: str) -> str:
    """Read and cache a SQL template by name."""
    return (SQL_DIR / f"{name}.sql").read_text()


def load_template(spec: TemplateSpec) -> str:
    """Substitute a mapping into its named SQL template.

    Values that are `sql.Composable` (`Identifier`, `Literal`, `SQL`) render
    through psycopg's own quoting and escaping. Plain strings pass through
    unescaped and must already be safe (for example a validated raw keyword
    like "true" or "false").
    """
    rendered: dict[str, str] = {}

    for key, value in spec["mapping"].items():
        match value:
            case sql.Composable():
                rendered[key] = value.as_string(None)
            case _:
                rendered[key] = value

    return Template(read_template(spec["path"])).substitute(rendered)
