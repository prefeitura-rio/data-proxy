from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest

from sync import s3
from sync.settings import settings
from tests.fixtures.types import STORE_KEYS, StoreStub
from tests.helpers.core import exactly


class TestClearS3Prefix:
    """A key that S3 refuses to delete is a failure, because the object stays behind."""

    @staticmethod
    def use(monkeypatch: pytest.MonkeyPatch, store: StoreStub) -> None:
        """Replace the client of the object store with the stub."""

        @asynccontextmanager
        async def client(*_: object) -> AsyncGenerator[StoreStub]:
            yield store

        monkeypatch.setattr(s3, "create_s3_client", client)

    async def test_deletes_every_key_of_the_page_in_one_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S3 takes up to 1000 keys in one request."""
        store = StoreStub({})
        self.use(monkeypatch, store)

        await s3.clear_s3_prefix("tmp/run")

        store.delete_objects.assert_awaited_once_with(
            Bucket=settings.S3_BUCKET,
            Delete={"Objects": [{"Key": key} for key in STORE_KEYS]},
        )

    @pytest.mark.parametrize(
        ("refused", "message"),
        [
            pytest.param(
                {"b": "AccessDenied"},
                "The object store refused to delete 1 objects under tmp/run: b (AccessDenied)",
                id="one-key-is-refused",
            ),
            pytest.param(
                {"a": "AccessDenied", "b": "InternalError", "c": "AccessDenied"},
                "The object store refused to delete 3 objects under tmp/run: "
                "a (AccessDenied), b (InternalError), c (AccessDenied)",
                id="every-key-is-refused",
            ),
        ],
    )
    async def test_raises_with_the_count_and_the_first_keys_that_s3_refused(
        self, monkeypatch: pytest.MonkeyPatch, refused: dict[str, str], message: str
    ) -> None:
        """The reply of S3 carries the failure, so the function must read it."""
        self.use(monkeypatch, StoreStub(refused))

        with pytest.raises(s3.S3DeleteError, match=exactly(message)):
            await s3.clear_s3_prefix("tmp/run")
