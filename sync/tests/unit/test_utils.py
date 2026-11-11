import pytest

from sync.settings import settings
from sync.utils import RemovalLimitError, json_digest, limit_removals, sha256_hex
from tests.helpers.core import exactly


class TestDigests:
    """The digests are fixed values, so stored signatures never change."""

    def test_hashes_text_as_sha256_hex(self) -> None:
        assert (
            sha256_hex("abc")
            == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )

    def test_ignores_the_key_order_of_json(self) -> None:
        expected = "21501dbaf73f5223934d22283f01caff4132bc1de4a9550c1ed0dffeb397a323"

        assert json_digest({"b": 1, "a": 2}) == expected
        assert json_digest({"a": 2, "b": 1}) == expected

    def test_changes_when_a_value_changes(self) -> None:
        assert (
            json_digest({"a": 2, "b": 3})
            == "11b6ee598608f1535294d5bd54862a39385ed1bf7fd78c3cea2cd2cfa2a1ea53"
        )

    def test_hashes_a_list(self) -> None:
        assert (
            json_digest(["x", "y"])
            == "8164c53b977060f6ba568eddee7e120cdac3ff783c0fff7c20e00a227e2c6c50"
        )


class TestLimitRemovals:
    """A step that would remove more than MAX_REMOVALS stops and names what it would remove."""

    @pytest.mark.parametrize(
        ("names", "message"),
        [
            pytest.param([], None, id="nothing-to-remove"),
            pytest.param(["a", "b"], None, id="at-the-limit"),
            pytest.param(
                ["a", "b", "c"],
                "3 tables would be removed, above MAX_REMOVALS=2: a, b, c",
                id="one-above-the-limit",
            ),
        ],
    )
    def test_raises_only_above_the_limit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        names: list[str],
        message: str | None,
    ) -> None:
        """The limit is the most that one step may remove."""
        monkeypatch.setattr(settings, "MAX_REMOVALS", 2)

        if message is None:
            limit_removals(names, "tables")
        else:
            with pytest.raises(RemovalLimitError, match=exactly(message)):
                limit_removals(names, "tables")
