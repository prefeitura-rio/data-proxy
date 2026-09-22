"""Shared type aliases used across Data Proxy modules."""

from collections.abc import Mapping, Sequence

from psycopg.sql import Composable

type JsonValue = (
    bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
)
type DatabaseRow = tuple[object, ...]
type TemplateValue = (
    str
    | bool
    | Composable
    | Sequence[str]
    | Mapping[str, TemplateValue]
    | Sequence[Mapping[str, TemplateValue]]
)
