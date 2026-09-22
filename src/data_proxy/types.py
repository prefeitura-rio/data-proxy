"""Shared type aliases used across Data Proxy modules."""

from collections.abc import Mapping, Sequence

from psycopg.sql import Composable

type TemplateValue = (
    str
    | bool
    | Composable
    | Sequence[str]
    | Mapping[str, object]
    | Sequence[Mapping[str, object]]
)
