"""Tests for temporary parquet bucket cleanup."""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dp.s3 import clear_bucket

if TYPE_CHECKING:  # pragma: no cover
    from types_aiobotocore_s3.type_defs import ObjectTypeDef


@pytest.mark.asyncio
async def test_clear_bucket_skips_objects_without_keys() -> None:
    """
    GIVEN: an S3 listing with valid objects and an entry without a key.
    WHEN: clear_bucket runs.
    THEN: it deletes only objects that have keys.
    """
    paginator = MagicMock()

    async def pages() -> AsyncIterator[dict[str, list[ObjectTypeDef]]]:
        yield {"Contents": [{}]}
        yield {
            "Contents": [
                {"Key": "app/people/data.parquet"},
                {},
                {"Key": "app/events/data.parquet"},
            ]
        }

    paginator.paginate.return_value = pages()
    client = MagicMock()
    client.get_paginator.return_value = paginator
    client.delete_objects = AsyncMock()

    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.create_client.return_value = context

    with patch("dp.s3.get_session", return_value=session):
        await clear_bucket()

    client.delete_objects.assert_awaited_once_with(
        Bucket="test-bucket",
        Delete={
            "Objects": [
                {"Key": "app/people/data.parquet"},
                {"Key": "app/events/data.parquet"},
            ]
        },
    )
