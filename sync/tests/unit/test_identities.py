from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from sync import identities
from sync.identities import remove_stale_identities, stale_users
from sync.settings import settings
from sync.utils import RemovalLimitError
from tests.helpers.core import exactly


class TestStaleUsers:
    """Pick the identities of the console that belong to no configured schema."""

    @pytest.mark.parametrize(
        ("users", "schemas", "expected"),
        [
            pytest.param(
                ["sig_sales", "sig_hr"], {"sales"}, ["sig_hr"], id="schema-removed"
            ),
            pytest.param(["sig_sales"], {"sales"}, [], id="every-schema-is-configured"),
            pytest.param(
                ["admin", "reader", "sig_gone"],
                {"sales"},
                ["sig_gone"],
                id="users-of-other-owners-stay",
            ),
            pytest.param(
                ["sig_", "sig_Bad-Name", "sig_x" + "y" * 60],
                set[str](),
                [],
                id="a-name-that-no-schema-can-have-stays",
            ),
            pytest.param(
                ["sig_b", "sig_a"], set[str](), ["sig_a", "sig_b"], id="no-schema-left"
            ),
        ],
    )
    def test_returns_the_sorted_names(
        self, users: list[str], schemas: set[str], expected: list[str]
    ) -> None:
        """Only a name that has the prefix and the form of a schema can go."""
        assert stale_users(users, schemas) == expected


class TestRemoveStaleIdentities:
    """Deleting S3 identities cannot be undone, so one run may remove only MAX_REMOVALS of them."""

    @pytest.fixture
    def remove(self, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
        """Stand in for the object store with three stale identities, and return the delete call."""

        @asynccontextmanager
        async def client(*_: object) -> AsyncGenerator[None]:
            yield

        remove = AsyncMock()
        monkeypatch.setattr(identities, "create_iam_client", client)
        monkeypatch.setattr(
            identities,
            "list_users",
            AsyncMock(return_value=["sig_a", "sig_b", "sig_c"]),
        )
        monkeypatch.setattr(identities, "remove_user", remove)

        return remove

    async def test_removes_the_stale_identities_up_to_the_limit(
        self, remove: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three identities at a limit of three go."""
        monkeypatch.setattr(settings, "MAX_REMOVALS", 3)

        assert await remove_stale_identities(set()) == ["sig_a", "sig_b", "sig_c"]
        assert remove.await_count == 3

    async def test_removes_nothing_above_the_limit(
        self, remove: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty configuration makes every identity stale, so the step stops before the first delete."""
        monkeypatch.setattr(settings, "MAX_REMOVALS", 2)

        with pytest.raises(
            RemovalLimitError,
            match=exactly(
                "3 S3 identities would be removed, above MAX_REMOVALS=2: sig_a, sig_b, sig_c"
            ),
        ):
            await remove_stale_identities(set())

        remove.assert_not_awaited()
