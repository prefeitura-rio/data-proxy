"""Shared Pydantic TypeAdapters for runtime validation across the sync pipeline."""

from pydantic import TypeAdapter

str_list: TypeAdapter[list[str]] = TypeAdapter(list[str])
