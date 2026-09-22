"""Unit tests for S3 object selection."""

from unittest.mock import patch

import pytest

from data_proxy.s3 import clear_s3_bucket
from tests.fixtures.unit import S3BucketDouble


class TestClearS3Bucket:
    """ClearS3Bucket behavior tests."""

    @pytest.mark.asyncio
    async def test_ignores_objects_without_keys(
        self, s3_bucket_double: S3BucketDouble
    ) -> None:
        """Ignore listed objects without keys."""
        bucket = s3_bucket_double
        with patch(
            "data_proxy.s3.create_s3_client", return_value=bucket.context
        ) as create:
            await clear_s3_bucket()
        assert create.call_args.args[1].startswith("http")
        assert bucket.paginator.bucket == "test-bucket"
        assert bucket.client.deletions == [
            {
                "Bucket": "test-bucket",
                "Delete": {
                    "Objects": [
                        {"Key": "app/people/data.parquet"},
                        {"Key": "app/events/data.parquet"},
                    ]
                },
            }
        ]
