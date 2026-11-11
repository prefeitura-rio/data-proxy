"""Shared helpers for the synchronization service."""

from hashlib import sha256
from json import dumps
from typing import TYPE_CHECKING

from .log import logger
from .settings import settings

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue


class RemovalLimitError(Exception):
    """A step would remove more things than MAX_REMOVALS allows."""


def sha256_hex(text: str) -> str:
    """Return the SHA-256 digest of the UTF-8 text as hex."""
    return sha256(text.encode()).hexdigest()


def json_digest(value: Mapping[str, JsonValue] | Sequence[JsonValue]) -> str:
    """Return the SHA-256 digest of the JSON with sorted keys, so key order never matters."""
    return sha256_hex(dumps(value, sort_keys=True))


def limit_removals(names: Sequence[str], what: str) -> None:
    """Say what a step is about to remove, and raise when that is more than MAX_REMOVALS.

    A wrong or empty configuration lists every table and every identity as removed. The step then stops
    before it deletes data that no one can bring back.
    """
    if len(names) <= settings.MAX_REMOVALS:
        return

    message = (
        f"{len(names)} {what} would be removed, above MAX_REMOVALS={settings.MAX_REMOVALS}: "
        f"{', '.join(names)}"
    )
    logger.warning(message)
    raise RemovalLimitError(message)
