"""The row policy has one implementation: `rls_where`, which the table function and the generic functions call.

The expected texts are what the Jinja macro `rls_where_clause` gave before the table function called `rls_where`. They
stay as fixed strings, so a change to the function that alters one row policy fails here, and the table view and the
aggregates can never read different rows for one user.

The order of the units inside a list is not part of the policy, because `IN ('a','b')` and `IN ('b','a')` match the same
rows. The comparison sorts each list first, so it fails for a missing unit, an extra unit or another predicate, and not
for another order.
"""

import json
import re
import time
from typing import Final

import pytest

from sync.views.mappings import rls_mappings_json, scope_path
from tests.constants import FULL, ROUTED_TABLE
from tests.fixtures.types import Postgres
from tests.helpers.core import scalar
from tests.helpers.routing import (
    aggregate,
    call_aggregate_function,
    grant_unit,
    insert_access_policy,
    install_aggregate_function,
    total,
)
from tests.helpers.state import put_state

pytestmark = pytest.mark.postgres

UNIT: Final = {"column": "unit_id", "unit_type": "unit", "duckdb_type": "VARCHAR"}
TEAM: Final = {"column": "grp", "unit_type": "team", "duckdb_type": "VARCHAR"}
MAPPINGS: Final[dict[str, list[dict[str, str]]]] = {
    "none": [],
    "one": [{**UNIT, "group": ""}],
    "group": [{**UNIT, "group": "1"}, {**TEAM, "group": "1"}],
    "two_rules": [{**UNIT, "group": ""}, {**TEAM, "group": ""}],
    "list": [
        {
            "column": "tags",
            "unit_type": "unit",
            "duckdb_type": "VARCHAR[]",
            "group": "",
        }
    ],
    "int": [
        {"column": "qty", "unit_type": "unit", "duckdb_type": "BIGINT", "group": ""}
    ],
}

# name of the mappings, list form, grants of the subject, and the WHERE text that the macro gave.
# None is no row, and an empty text is every row.
MACRO_OUTPUT: Final[list[tuple[str, bool, str, str | None]]] = [
    ("group", True, "both", ""),
    ("group", True, "nogrant", None),
    ("group", True, "star", ""),
    (
        "group",
        True,
        "unit",
        "WHERE ((CAST(grp AS VARCHAR) IN (SELECT unnest(['a'])) AND CAST(unit_id AS VARCHAR) IN (SELECT unnest(['u1','u2']))))",
    ),
    ("group", False, "both", ""),
    ("group", False, "nogrant", None),
    ("group", False, "star", ""),
    ("group", False, "unit", "WHERE ((grp IN ('a') AND unit_id IN ('u1','u2')))"),
    ("int", True, "both", ""),
    ("int", True, "nogrant", None),
    ("int", True, "star", ""),
    (
        "int",
        True,
        "unit",
        "WHERE (CAST(qty AS VARCHAR) IN (SELECT unnest(['u1','u2'])))",
    ),
    ("int", False, "both", ""),
    ("int", False, "nogrant", None),
    ("int", False, "star", ""),
    ("int", False, "unit", "WHERE (qty IN ('u1','u2'))"),
    ("list", True, "both", ""),
    ("list", True, "nogrant", None),
    ("list", True, "star", ""),
    ("list", True, "unit", "WHERE (list_has_any(tags, ['u1','u2']))"),
    ("list", False, "both", ""),
    ("list", False, "nogrant", None),
    ("list", False, "star", ""),
    ("list", False, "unit", "WHERE (list_has_any(tags, ['u1','u2']))"),
    ("none", True, "both", ""),
    ("none", True, "nogrant", None),
    ("none", True, "star", ""),
    ("none", True, "unit", None),
    ("none", False, "both", ""),
    ("none", False, "nogrant", None),
    ("none", False, "star", ""),
    ("none", False, "unit", None),
    ("one", True, "both", ""),
    ("one", True, "nogrant", None),
    ("one", True, "star", ""),
    (
        "one",
        True,
        "unit",
        "WHERE (CAST(unit_id AS VARCHAR) IN (SELECT unnest(['u1','u2'])))",
    ),
    ("one", False, "both", ""),
    ("one", False, "nogrant", None),
    ("one", False, "star", ""),
    ("one", False, "unit", "WHERE (unit_id IN ('u1','u2'))"),
    ("two_rules", True, "both", ""),
    ("two_rules", True, "nogrant", None),
    ("two_rules", True, "star", ""),
    (
        "two_rules",
        True,
        "unit",
        "WHERE (CAST(unit_id AS VARCHAR) IN (SELECT unnest(['u1','u2'])) OR CAST(grp AS VARCHAR) IN (SELECT unnest(['a'])))",
    ),
    ("two_rules", False, "both", ""),
    ("two_rules", False, "nogrant", None),
    ("two_rules", False, "star", ""),
    ("two_rules", False, "unit", "WHERE (unit_id IN ('u1','u2') OR grp IN ('a'))"),
]


LIST: Final = re.compile(
    r"(?P<open>\(|\[)(?P<items>'[^']*'(?:,'[^']*')*)(?P<close>\)|\])"
)


def normalize(text: str | None) -> str | None:
    """Sort the units of every list in a policy text, so two orders of one list read as one."""
    if text is None:
        return None
    return LIST.sub(
        lambda match: (
            match["open"]
            + ",".join(sorted(re.findall(r"'[^']*'", match["items"])))
            + match["close"]
        ),
        text,
    )


async def grant(pg: Postgres, grants: str) -> str:
    """Give the subject the grants of one case and give the name of the subject."""
    if grants in ("unit", "both"):
        await insert_access_policy(pg, "u", "unit", "u1")
        await insert_access_policy(pg, "u", "unit", "u2")
        await insert_access_policy(pg, "u", "team", "a")
    if grants in ("star", "both"):
        await insert_access_policy(pg, "u", "*", "*")
    return "u" if grants != "nogrant" else "nobody"


@pytest.mark.parametrize(
    ("name", "list_form", "grants", "expected"),
    [pytest.param(*case, id=f"{case[0]}-{case[1]}-{case[2]}") for case in MACRO_OUTPUT],
)
async def test_gives_the_where_text_that_the_macro_gave(
    aggregate_function: Postgres,
    name: str,
    list_form: bool,
    grants: str,
    expected: str | None,
) -> None:
    """Every rule shape, with each grant of a subject, gives the same text as before."""
    pg = aggregate_function
    schema = pg.namespace.schema
    await pg.connection.execute(
        b"SELECT set_config('rbac.schemas', ',x,', false), set_config('rbac.permissions', '', false)"
    )
    subject = await grant(pg, grants)
    mappings = json.dumps(
        rls_mappings_json(
            MAPPINGS[name],
        )
    )

    actual = await scalar(
        pg,
        "SELECT data_proxy.rls_where(%s, %s, %s, %s::jsonb, %s)",
        schema,
        scope_path(schema, "t"),
        subject,
        mappings,
        list_form,
    )

    assert isinstance(actual, str | None)
    assert normalize(actual) == normalize(expected)


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        pytest.param("IN ('b','a')", "IN ('a','b')", True, id="two-orders-of-one-list"),
        pytest.param(
            "unnest(['u2','u1','u3'])", "unnest(['u3','u1','u2'])", True, id="list-form"
        ),
        pytest.param(
            "list_has_any(tags, ['b','a'])",
            "list_has_any(tags, ['a','b'])",
            True,
            id="list-column",
        ),
        pytest.param(
            "(grp IN ('b','a') AND unit_id IN ('y','x'))",
            "(grp IN ('a','b') AND unit_id IN ('x','y'))",
            True,
            id="two-lists-in-a-group",
        ),
        pytest.param("IN ('a')", "IN ('a','b')", False, id="a-unit-is-missing"),
        pytest.param("IN ('a','b')", "IN ('a','c')", False, id="another-unit"),
        pytest.param("IN ('a','b')", "NOT IN ('a','b')", False, id="another-predicate"),
        pytest.param(
            "(grp IN ('a') AND unit_id IN ('x'))",
            "(grp IN ('x') AND unit_id IN ('a'))",
            False,
            id="the-lists-swap-columns",
        ),
        pytest.param("", "", True, id="every-row"),
        pytest.param(None, None, True, id="no-row"),
        pytest.param(None, "", False, id="no-row-is-not-every-row"),
    ],
)
def test_the_comparison_ignores_the_order_of_a_list_and_nothing_else(
    first: str | None, second: str | None, same: bool
) -> None:
    """A sorted list hides only the order, so a missing unit, an extra unit or another predicate still fails."""
    assert (normalize(first) == normalize(second)) is same


async def test_the_table_function_and_the_aggregate_read_the_same_rows(
    aggregate_function: Postgres,
) -> None:
    """One subject with the unit u1 sums the same rows through the aggregate as the view shows, because one function builds both policies."""
    pg = aggregate_function
    await install_aggregate_function(pg, has_rls=True, fallbacks=[])
    await put_state(pg, ROUTED_TABLE, FULL)
    await grant_unit(pg, "u")

    answer, _ = await call_aggregate_function(pg, aggregate(total()))

    assert answer == [{"n": 7}]


async def test_a_long_list_of_grants_stays_in_the_same_order_of_time(
    aggregate_function: Postgres,
) -> None:
    """With 2,000 grants for one subject the call finishes in a fraction of a second. The old inline form took about the same time.

    The test records the time in the failure message and asserts only a loose bound, so a slow machine does not make it flaky.
    """
    pg = aggregate_function
    schema = pg.namespace.schema
    await pg.connection.execute(
        b"SELECT set_config('rbac.schemas', ',x,', false), set_config('rbac.permissions', '', false)"
    )
    for index in range(2000):
        await insert_access_policy(pg, "u", "unit", f"unit_{index}")
    mappings = json.dumps(rls_mappings_json(MAPPINGS["one"]))

    started = time.perf_counter()
    text = await scalar(
        pg,
        "SELECT data_proxy.rls_where(%s, %s, %s, %s::jsonb, true)",
        schema,
        scope_path(schema, "t"),
        "u",
        mappings,
    )
    seconds = time.perf_counter() - started

    assert isinstance(text, str)
    assert text.count("'unit_") == 2000
    assert seconds < 2, f"rls_where took {seconds:.3f} seconds for 2,000 grants"
