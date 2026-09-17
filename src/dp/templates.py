"""Substitute mapping into a cached SQL template and return the final SQL."""

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import LiteralString, cast

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from psycopg.sql import Composable

from .types import TemplateValue

SQL_DIR = Path(__file__).parent / "sql"


@lru_cache
def jinja_environment(root: Path) -> Environment:
    """Return one strict Jinja environment for a SQL template directory."""
    return Environment(
        loader=FileSystemLoader(root),
        undefined=StrictUndefined,
        autoescape=select_autoescape(default=False, default_for_string=False),
        keep_trailing_newline=True,
    )


@lru_cache
def read_template(name: str, root: Path) -> str:
    """Read and cache a SQL template by name from one SQL directory."""
    return (root / f"{name}.sql").read_text()


def render_template(
    path: str,
    mapping: Mapping[str, TemplateValue],
    *,
    root: Path = SQL_DIR,
) -> LiteralString:
    """Render one strict Jinja SQL template with composable values converted to SQL."""
    rendered: dict[str, TemplateValue] = {}

    for key, value in mapping.items():
        match value:
            case Composable():
                rendered[key] = value.as_string(None)
            case _:
                rendered[key] = value

    template = jinja_environment(root).get_template(f"{path}.sql").render(rendered)

    return cast("LiteralString", template)
