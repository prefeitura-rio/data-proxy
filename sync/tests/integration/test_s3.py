from io import BytesIO

import pytest

from sync.s3 import clear_s3_prefix
from sync.settings import settings
from tests.fixtures.types import Silo


@pytest.mark.s3
async def test_clearing_an_s3_prefix_removes_the_objects_under_it(
    silo: Silo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clear the scratch prefix of one run without touching anything else."""
    for name in ("a", "b"):
        data = BytesIO(f"data-{name}".encode())
        silo.client.put_object(
            "test-bucket", f"tmp/test/{name}.parquet", data, data.getbuffer().nbytes
        )
    monkeypatch.setattr(settings, "S3_ENDPOINT", f"{silo.host}:{silo.port}")
    monkeypatch.setattr(settings, "S3_USE_SSL", False)
    monkeypatch.setattr(settings, "S3_ACCESS_KEY", "minioadmin")
    monkeypatch.setattr(settings, "S3_SECRET_KEY", "minioadmin")

    await clear_s3_prefix("tmp/test")

    assert list(silo.client.list_objects("test-bucket", prefix="tmp/test/")) == []
