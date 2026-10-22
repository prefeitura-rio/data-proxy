import pytest

from sync.identities import IDENTITY_PREFIX, stale_users


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

    def test_the_prefix_is_the_one_that_the_console_uses(self) -> None:
        """The console names an identity sig_ and the schema."""
        assert IDENTITY_PREFIX == "sig_"
