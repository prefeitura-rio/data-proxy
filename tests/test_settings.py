"""Tests for application settings validation."""

import pytest
from pydantic import ValidationError

from dp.settings import Settings


def test_worker_visibility_timeout_defaults_to_fifteen_minutes() -> None:
    """The worker visibility timeout defaults to fifteen minutes."""
    assert Settings().WORKER_VISIBILITY_TIMEOUT_MS == 900_000


def test_finalizer_visibility_timeout_defaults_to_fifteen_minutes() -> None:
    """The finalizer visibility timeout defaults to fifteen minutes."""
    assert Settings().FINALIZER_VISIBILITY_TIMEOUT_MS == 900_000


@pytest.mark.parametrize("value", [0, -1])
def test_finalizer_visibility_timeout_must_be_positive(value: int) -> None:
    """The finalizer visibility timeout rejects non-positive values."""
    with pytest.raises(ValidationError):
        Settings(FINALIZER_VISIBILITY_TIMEOUT_MS=value)


@pytest.mark.parametrize("value", [0, -1])
def test_worker_visibility_timeout_must_be_positive(value: int) -> None:
    """The worker visibility timeout rejects non-positive values."""
    with pytest.raises(ValidationError):
        Settings(WORKER_VISIBILITY_TIMEOUT_MS=value)
