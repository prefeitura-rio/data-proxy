"""Substitute mapping into a cached SQL template and return the final SQL."""

from functools import lru_cache
from pathlib import Path
from string import Template
from typing import TypedDict

from psycopg.sql import Composable, Identifier, Literal

from .models import (
    AllSelection,
    RangeSelection,
    RemainderSelection,
    TaskSelection,
    TimeRangeSelection,
)

SQL_DIR = Path(__file__).parent / "sql"


class TemplateSpec(TypedDict):
    """Template path and substitution values for one SQL statement."""

    path: str
    mapping: dict[str, str | Composable]


@lru_cache
def read_template(name: str) -> str:
    """Read and cache a SQL template by name."""
    return (SQL_DIR / f"{name}.sql").read_text()


def load_template(spec: TemplateSpec) -> str:
    """Substitute a mapping into its named SQL template.

    Values that are `Composable` (`Identifier`, `Literal`, `SQL`) render
    through psycopg's own quoting and escaping. Plain strings pass through
    unescaped and must already be safe (for example a validated raw keyword
    like "true" or "false").
    """
    rendered: dict[str, str] = {}

    for key, value in spec["mapping"].items():
        match value:
            case Composable():
                rendered[key] = value.as_string(None)
            case _:
                rendered[key] = value

    return Template(read_template(spec["path"])).substitute(rendered)


def selection_fields(selection: TaskSelection) -> dict[str, str | Composable]:
    """Return the column and bound literals encoded by one task selection."""
    match selection:
        case AllSelection():
            return {}
        case (
            RangeSelection(column=column, lower=lower, upper=upper)
            | TimeRangeSelection(column=column, lower=lower, upper=upper)
        ):
            return {
                "column": Identifier(column),
                "lower": Literal(lower),
                "upper": Literal(upper),
            }
        case RemainderSelection(column=column, start=start, end=end):
            return {
                "column": Identifier(column),
                "lower": Literal(start),
                "upper": Literal(end),
            }
