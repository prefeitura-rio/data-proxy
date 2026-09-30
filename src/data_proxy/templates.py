"""Substitute mapping into a cached SQL template and return the final SQL."""

from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import LiteralString, cast

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from psycopg.sql import Composable

from .constants import SQL_DIR
from .types import TemplateValue


@lru_cache
def jinja_environment(root: Path) -> Environment:
    """Return one strict Jinja environment for a SQL template directory."""
    return Environment(
        loader=FileSystemLoader(root),
        undefined=StrictUndefined,
        autoescape=select_autoescape(default=False, default_for_string=False),
        keep_trailing_newline=True,
    )


def to_sql(value: TemplateValue) -> TemplateValue:
    """Convert composables, including those nested in lists and mappings, to SQL."""
    match value:
        case Composable():
            return value.as_string(None)
        case str() | bool():
            return value
        case Mapping():
            return {key: to_sql(item) for key, item in value.items()}
        case Sequence():
            return [to_sql(item) for item in value]


def render_template(
    path: str,
    mapping: Mapping[str, TemplateValue],
    *,
    root: Path = SQL_DIR,
) -> LiteralString:
    """Render one strict Jinja SQL template with composable values converted to SQL."""
    rendered = {key: to_sql(value) for key, value in mapping.items()}
    template = jinja_environment(root).get_template(f"{path}.sql").render(rendered)

    return cast("LiteralString", template)
