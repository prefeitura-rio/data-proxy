"""The schema access: one policy table and one log for every project, with a scope of one ltree."""

import itertools

import pytest

from tests.fixtures.console import refusal
from tests.fixtures.types import Postgres
from tests.helpers.core import scalar
from tests.helpers.rbac import act_as, revision, seed_grants

INSUFFICIENT = "42501"
CHECK_VIOLATION = "23514"
NOT_NULL_VIOLATION = "23502"
UNIQUE_VIOLATION = "23505"

SCOPES = ["", "sales", "sales.orders", "sales.orders.items", "hr.staff"]
TOKENS: dict[str, tuple[str, list[str], list[str]]] = {
    "admin": ("dp_admin", [], []),
    "project": ("dp_policy", [], ["sales"]),
    "schema": ("dp_policy", ["sales__orders"], []),
    "no-permission": ("dp_api", ["sales__orders"], ["sales"]),
}
REACH = {
    "admin": set(SCOPES),
    "project": {"sales", "sales.orders", "sales.orders.items"},
    "schema": {"sales.orders", "sales.orders.items"},
    "no-permission": set(),
}
CASES = [
    pytest.param(
        token,
        scope,
        scope in REACH[token],
        id=f"{token}-at-{'all' if len(scope) == 0 else scope}",
    )
    for token, scope in itertools.product(TOKENS, SCOPES)
]


@pytest.mark.postgres
class TestPolicyRows:
    """A token acts on a grant when it has the permission of the action and reaches the scope of the grant."""

    @pytest.mark.parametrize(("token", "scope", "reaches"), CASES)
    async def test_a_token_reads_the_grants_of_its_reach(
        self, console_api: Postgres, token: str, scope: str, reaches: bool
    ) -> None:
        """Hide a grant outside the reach, and every grant from a token without read_policy."""
        await seed_grants(console_api, SCOPES)
        await act_as(console_api, *TOKENS[token])

        seen = await scalar(
            console_api,
            "SELECT count(*) FROM access.policy WHERE scope = %s::ltree",
            scope,
        )

        assert seen == (1 if reaches else 0)

    @pytest.mark.parametrize(("token", "scope", "reaches"), CASES)
    async def test_a_token_adds_a_grant_in_its_reach(
        self, console_api: Postgres, token: str, scope: str, reaches: bool
    ) -> None:
        """Refuse the insert of a grant that the token could not read back."""
        await act_as(console_api, *TOKENS[token])

        state = await refusal(
            console_api,
            "INSERT INTO access.policy VALUES ('bia', %s::ltree, 'cpf', '2')",
            scope,
        )

        assert state == (None if reaches else INSUFFICIENT)

    @pytest.mark.parametrize(("token", "scope", "reaches"), CASES)
    async def test_a_token_changes_a_grant_in_its_reach(
        self, console_api: Postgres, token: str, scope: str, reaches: bool
    ) -> None:
        """A hidden grant stays as it is, and the update does not say that it exists."""
        await seed_grants(console_api, SCOPES)
        await act_as(console_api, *TOKENS[token])

        cursor = await console_api.connection.execute(
            b"UPDATE access.policy SET unit_id = '9' WHERE scope = %s::ltree",
            (scope,),
        )

        assert cursor.rowcount == (1 if reaches else 0)

    @pytest.mark.parametrize(("token", "scope", "reaches"), CASES)
    async def test_a_token_deletes_a_grant_in_its_reach(
        self, console_api: Postgres, token: str, scope: str, reaches: bool
    ) -> None:
        """A hidden grant stays as it is."""
        await seed_grants(console_api, SCOPES)
        await act_as(console_api, *TOKENS[token])

        cursor = await console_api.connection.execute(
            b"DELETE FROM access.policy WHERE scope = %s::ltree", (scope,)
        )

        assert cursor.rowcount == (1 if reaches else 0)

    async def test_an_update_cannot_move_a_grant_out_of_the_reach(
        self, console_api: Postgres
    ) -> None:
        """The new row must pass the same check as an insert."""
        await seed_grants(console_api, SCOPES)
        await act_as(console_api, *TOKENS["schema"])

        state = await refusal(
            console_api,
            "UPDATE access.policy SET scope = 'hr.staff' WHERE scope = 'sales.orders'",
        )

        assert state == INSUFFICIENT


@pytest.mark.postgres
class TestPolicyColumns:
    """The columns refuse what no grant can mean."""

    @pytest.mark.parametrize(
        ("statement", "state"),
        [
            pytest.param(
                "INSERT INTO access.policy VALUES ('a', 'p.s.t', 'cpf', '1')",
                None,
                id="a-table-scope",
            ),
            pytest.param(
                "INSERT INTO access.policy VALUES ('a', 'p.s.t.u', 'cpf', '1')",
                CHECK_VIOLATION,
                id="a-scope-of-four-labels",
            ),
            pytest.param(
                "INSERT INTO access.policy VALUES ('a', 'p', NULL, '1')",
                NOT_NULL_VIOLATION,
                id="no-unit-type",
            ),
            pytest.param(
                "INSERT INTO access.policy VALUES ('a', 'p', 'cpf', NULL)",
                NOT_NULL_VIOLATION,
                id="no-unit-id",
            ),
            pytest.param(
                "INSERT INTO access.policy VALUES ('', 'p', 'cpf', '1')",
                CHECK_VIOLATION,
                id="an-empty-subject",
            ),
        ],
    )
    async def test_a_grant_has_a_scope_of_up_to_three_labels_and_a_unit(
        self, console_api: Postgres, statement: str, state: str | None
    ) -> None:
        """Keep the scopes to `*`, `P`, `P.S` and `P.S.T`, and the unit whole."""
        assert await refusal(console_api, statement) == state

    async def test_the_same_grant_is_added_once(self, console_api: Postgres) -> None:
        """A grant of one subject, scope and unit is one row."""
        await seed_grants(console_api, SCOPES)

        state = await refusal(
            console_api, "INSERT INTO access.policy VALUES ('ana', 'sales', 'cpf', '1')"
        )

        assert state == UNIQUE_VIOLATION


@pytest.mark.postgres
class TestPolicyLog:
    """The log has one row for each change, and only the prune function removes rows."""

    async def test_every_change_is_logged_with_the_old_row_of_an_update_and_a_delete(
        self, console_api: Postgres
    ) -> None:
        """Write the new row of an insert and the old row of an update and a delete."""
        await console_api.connection.execute(
            b"INSERT INTO access.policy VALUES ('ana', 'sales.orders', 'unit', 'u1')"
        )
        await console_api.connection.execute(b"UPDATE access.policy SET unit_id = 'u2'")
        await console_api.connection.execute(b"DELETE FROM access.policy")

        cursor = await console_api.connection.execute(
            b"SELECT subject, scope::text, unit_type, unit_id, action FROM access.log ORDER BY changed_at, action"
        )

        assert sorted(await cursor.fetchall()) == [
            ("ana", "sales.orders", "unit", "u1", "insert"),
            ("ana", "sales.orders", "unit", "u1", "update"),
            ("ana", "sales.orders", "unit", "u2", "delete"),
        ]

    @pytest.mark.parametrize(("token", "scope", "reaches"), CASES)
    async def test_a_token_reads_the_log_of_its_reach(
        self, console_api: Postgres, token: str, scope: str, reaches: bool
    ) -> None:
        """Show a log row to a token with read_policy that reaches its scope."""
        await seed_grants(console_api, SCOPES)
        await act_as(console_api, *TOKENS[token])

        seen = await scalar(
            console_api,
            "SELECT count(*) FROM access.log WHERE scope = %s::ltree",
            scope,
        )

        assert seen == (1 if reaches else 0)

    async def test_the_log_refuses_an_update_and_a_delete(
        self, console_api: Postgres
    ) -> None:
        """A log that an editor can change proves nothing."""
        await seed_grants(console_api, SCOPES)

        assert (
            await refusal(console_api, "UPDATE access.log SET subject = 'eve'")
            == INSUFFICIENT
        )
        assert await refusal(console_api, "DELETE FROM access.log") == INSUFFICIENT

    async def test_a_token_with_no_grant_on_the_log_writes_none(
        self, console_api: Postgres
    ) -> None:
        """The database role of the requests reads the log and writes nothing in it."""
        await act_as(console_api, *TOKENS["admin"])

        state = await refusal(
            console_api,
            "INSERT INTO access.log VALUES ('eve', 'sales', 'cpf', '1', 'insert', now())",
        )

        assert state == INSUFFICIENT

    async def test_prune_log_removes_the_rows_older_than_the_retention_only(
        self, console_api: Postgres
    ) -> None:
        """Keep the recent rows, and stop only when the procedure runs."""
        await console_api.connection.execute(
            b"INSERT INTO access.log VALUES ('old', 'sales', 'cpf', '1', 'insert', now() - '100 days'::interval),"
            b" ('new', 'sales', 'cpf', '1', 'insert', now() - '1 day'::interval)"
        )

        await console_api.connection.execute(b"CALL access.prune_log('90 days')")

        cursor = await console_api.connection.execute(b"SELECT subject FROM access.log")
        assert await cursor.fetchall() == [("new",)]


@pytest.mark.postgres
class TestPolicyCommit:
    """A write to the policy moves the revision once and waits for the standbys."""

    async def test_each_write_statement_moves_the_revision_once(
        self, console_api: Postgres
    ) -> None:
        """The proxy learns of a change from the revision, so a statement of many rows moves it by one."""
        revisions = [await revision(console_api)]

        for statement in (
            "INSERT INTO access.policy VALUES ('ana', 'sales', 'unit', 'u1'), ('bia', 'sales', 'unit', 'u2')",
            "UPDATE access.policy SET unit_id = 'u3'",
            "DELETE FROM access.policy",
        ):
            await console_api.connection.execute(statement.encode())
            revisions.append(await revision(console_api))

        assert [after - before for before, after in itertools.pairwise(revisions)] == [
            1,
            1,
            1,
        ]

    async def test_a_write_waits_for_the_standbys_in_its_own_transaction(
        self, console_api: Postgres
    ) -> None:
        """Raise synchronous_commit for the transaction of the write and no other."""
        before = await scalar(
            console_api, "SELECT current_setting('synchronous_commit')"
        )
        await console_api.connection.execute(
            b"INSERT INTO access.policy VALUES ('ana', 'sales', 'unit', 'u1')"
        )
        during = await scalar(
            console_api, "SELECT current_setting('synchronous_commit')"
        )
        await console_api.connection.rollback()
        after = await scalar(
            console_api, "SELECT current_setting('synchronous_commit')"
        )

        assert (before, during, after) == ("on", "remote_apply", "on")
