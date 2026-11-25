"""The conventions that every generated RPC of a data schema follows, read from the catalog of a reconciled schema."""

from typing import Final

import pytest

from sync.views.stages import SCHEMA_FUNCTIONS
from tests.fixtures.types import Postgres
from tests.helpers.core import fetch_all
from tests.helpers.routing import install_schema_rpc

pytestmark = pytest.mark.postgres

# PostgREST reads these names of a query string as filters, so a function cannot take an argument of the same name.
RESERVED_ARGUMENTS: Final = frozenset(
    {"select", "order", "limit", "offset", "columns", "on_conflict"}
)
GENERATED: Final = ("agg", "hll", "changes")


@pytest.fixture
async def wrappers(routing: Postgres) -> Postgres:
    """Create the wrappers of the generic functions in the test schema."""
    await install_schema_rpc(routing)
    return routing


async def catalog(pg: Postgres) -> list[tuple[object, ...]]:
    """List the wrappers of the test schema with their arguments, security and grants."""
    rows = await fetch_all(
        pg,
        "postgres/select_rpc_catalog",
        mapping={},
        params={"schema_name": pg.namespace.schema},
    )
    return [tuple(row) for row in rows]


def test_every_function_name_has_one_word() -> None:
    """The names that the sync creates in a data schema carry no prefix and no underscore."""
    assert all("_" not in name for name in SCHEMA_FUNCTIONS)


async def test_no_function_of_the_schema_has_the_old_prefix(wrappers: Postgres) -> None:
    """The wrappers and the helpers that the sync installs leave no function that starts with dl_."""
    rows = await fetch_all(
        wrappers,
        "postgres/select_function_names",
        mapping={},
        params={"schema_name": wrappers.namespace.schema},
    )

    assert [row[0] for row in rows if str(row[0]).startswith("dl_")] == []


async def test_every_wrapper_is_in_the_list_of_managed_functions(
    wrappers: Postgres,
) -> None:
    """A wrapper that the sync does not list is never dropped with its schema or found by the reconcile."""
    names = {str(row[0]) for row in await catalog(wrappers)}

    assert set(GENERATED) <= names
    assert set(GENERATED) <= set(SCHEMA_FUNCTIONS)


async def test_no_wrapper_takes_an_argument_that_postgrest_reads_as_a_filter(
    wrappers: Postgres,
) -> None:
    """A query string key that PostgREST reserves never reaches the function as an argument."""
    rows = await catalog(wrappers)

    named = {str(row[0]): {str(name) for name in row[1]} for row in rows}  # type: ignore[attr-defined]
    for function in GENERATED:
        assert named[function].isdisjoint(RESERVED_ARGUMENTS), function


async def test_every_wrapper_runs_as_its_owner_with_a_fixed_search_path(
    wrappers: Postgres,
) -> None:
    """A SECURITY DEFINER function must fix its search path, or a caller could shadow the functions that it calls."""
    rows = await catalog(wrappers)

    assert {str(row[0]): (row[2], row[3]) for row in rows if row[0] in GENERATED} == {
        function: (True, True) for function in GENERATED
    }


async def test_only_the_request_role_can_call_a_wrapper(wrappers: Postgres) -> None:
    """The functions are revoked from public and granted to the role that every token switches to."""
    rows = await catalog(wrappers)

    assert {str(row[0]): tuple(row[4]) for row in rows if row[0] in GENERATED} == {  # type: ignore[arg-type]
        function: (False, True) for function in GENERATED
    }
