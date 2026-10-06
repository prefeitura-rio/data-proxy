"""Shared helpers for the synchronization service."""

from collections.abc import Mapping, Sequence
from hashlib import sha256
from json import dumps

from pydantic import JsonValue


def sha256_hex(text: str) -> str:
    """Return the SHA-256 digest of the UTF-8 text as hex."""
    return sha256(text.encode()).hexdigest()


def json_digest(value: Mapping[str, JsonValue] | Sequence[JsonValue]) -> str:
    """Return the SHA-256 digest of the JSON with sorted keys, so key order never matters."""
    return sha256_hex(dumps(value, sort_keys=True))
