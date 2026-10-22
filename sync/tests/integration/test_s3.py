from io import BytesIO

import pytest

from sync.s3 import clear_s3_prefix
from sync.settings import settings
from tests.constants import S3_ACCESS_KEY, S3_SECRET_KEY
from tests.fixtures.types import Storage


@pytest.mark.s3
async def test_clearing_an_s3_prefix_removes_the_objects_under_it(
    storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clear the scratch prefix of one run without touching anything else."""
    for name in ("a", "b"):
        data = BytesIO(f"data-{name}".encode())
        storage.client.put_object(
            "test-bucket", f"tmp/test/{name}.parquet", data, data.getbuffer().nbytes
        )
    monkeypatch.setattr(settings, "S3_ENDPOINT", f"{storage.host}:{storage.port}")
    monkeypatch.setattr(settings, "S3_USE_SSL", False)
    monkeypatch.setattr(settings, "S3_ACCESS_KEY", S3_ACCESS_KEY)
    monkeypatch.setattr(settings, "S3_SECRET_KEY", S3_SECRET_KEY)

    await clear_s3_prefix("tmp/test")

    remaining = storage.client.list_objects("test-bucket", prefix="tmp/test/")

    assert [item.object_name for item in remaining if not item.is_dir] == []
