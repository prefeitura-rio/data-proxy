"""Differential filter checks against a real PostgREST service."""

import base64
import hashlib
import hmac
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from tests.constants import POSTGREST_AUTH
from tests.fixtures.types import Postgrest

ROOT = Path(__file__).parents[2]


def token_for(subject: str) -> str:
    """Build the HS256 token accepted by the test PostgREST instance."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"role": "anon", "sub": subject}, separators=(",", ":")).encode()
    ).rstrip(b"=")
    message = header + b"." + payload
    signature = hmac.new(POSTGREST_AUTH.encode(), message, hashlib.sha256).digest()
    return (
        ".".join(part.decode() for part in (message.split(b".")))
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    )


def postgrest_rows(
    service: Postgrest,
    query: str,
    filter_header: str | None = None,
    token: str | None = None,
) -> list[dict[str, str | int | bool]]:
    """Read RPC rows, optionally with a pushed filter document."""
    headers = {"Accept-Profile": "pushdown"}
    if filter_header is not None:
        headers["X-DuckLake-Filter"] = filter_header
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    request = Request(  # noqa: S310
        service.url + "/rpc/items" + ("?" + query if query else ""),
        headers=headers,
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310
            rows = json.loads(response.read())
    except HTTPError as error:
        raise AssertionError(error.read().decode()) from error
    return rows


def parsed_filter(query: str) -> str:
    """Run the real TypeScript parser used by the proxy."""
    completed = subprocess.run(  # noqa: S603
        ["node", "proxy/peggy/parse.mjs", query],  # noqa: S607
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _comparison(column: str, sep: str, op: str, val: str) -> str:
    """Render one comparison with the given column-operator separator."""
    return f"{column}{sep}{op}.{val}"


def _in_list(members: list[str], sep: str) -> str:
    """Render one id=in.(...) list with the given separator."""
    return "id" + sep + "in.(" + ",".join(members) + ")"


def _logical(left: str, right: str, op: str, negated: bool, sep: str) -> str:
    """Render one and/or group, optionally negated, with the given separator."""
    prefix = "not." if negated else ""
    return f"{prefix}{op}{sep}({left},{right})"


def comparison_for_column(column: str, sep: str) -> st.SearchStrategy[str]:
    """Return a strategy for one comparison over a known column."""
    if column == "id":
        operator = st.sampled_from(["eq", "neq", "gt", "gte", "lt", "lte"])
        value = st.integers(0, 100).map(str)
    elif column == "name":
        operator = st.sampled_from(["eq", "neq", "like", "ilike"])
        value = st.sampled_from(["Rio", "Tokyo", "Paris", "rio", "*rio*", "*o*"])
    elif column == "active":

        def build(op: str, val: str) -> str:
            return _comparison(column, sep, op, val)

        return st.one_of(
            st.builds(
                build,
                st.just("is"),
                st.sampled_from(["true", "false", "null", "not_null"]),
            ),
            st.builds(build, st.just("eq"), st.sampled_from(["true", "false"])),
        )
    else:
        raise ValueError(f"unsupported test column: {column}")

    def build(op: str, val: str) -> str:
        return _comparison(column, sep, op, val)

    return st.builds(build, operator, value)


def comparison(sep: str) -> st.SearchStrategy[str]:
    """Return a strategy for a single comparison over a known column."""
    return st.one_of(
        comparison_for_column("id", sep),
        comparison_for_column("name", sep),
        comparison_for_column("active", sep),
    )


def in_comparison(sep: str) -> st.SearchStrategy[str]:
    """Return a strategy for id=in.(...) over integer members."""
    values = st.lists(st.integers(0, 100).map(str), min_size=1, max_size=4, unique=True)

    def build(members: list[str]) -> str:
        return _in_list(members, sep)

    return values.map(build)


def extend_logical(
    children: st.SearchStrategy[str], sep: str
) -> st.SearchStrategy[str]:
    """Extend the recursive strategy with one logical group."""

    def build(left: str, right: str, op: str, negated: bool) -> str:
        return _logical(left, right, op, negated, sep)

    return st.builds(
        build,
        children,
        children,
        st.sampled_from(["and", "or"]),
        st.booleans(),
    )


def expression() -> st.SearchStrategy[str]:
    """Return a strategy for a dot-form expression usable inside a group."""
    leaf = st.one_of(comparison("."), in_comparison("."))

    def extend(children: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
        return extend_logical(children, "")

    return st.recursive(leaf, extend, max_leaves=4)


def filters() -> st.SearchStrategy[str]:
    """Return a strategy for a top-level PostgREST filter."""
    return st.one_of(
        comparison("="),
        in_comparison("="),
        extend_logical(expression(), "="),
    )


@pytest.mark.postgrest
@settings(max_examples=30, deadline=None)
@given(query=filters())
def test_filter_pushdown_matches_postgrest(
    postgrest: Postgrest,
    query: str,
) -> None:
    """The pushed result matches PostgREST's native filter result."""
    native = postgrest_rows(postgrest, query)
    pushed = postgrest_rows(postgrest, query, parsed_filter(query))

    assert pushed == native


@pytest.mark.postgrest
def test_filter_pushdown_preserves_postgrest_rls(
    postgrest: Postgrest,
) -> None:
    """The pushed result keeps the native PostgREST row policy."""
    query = "id=gte.1"
    token = token_for("alice")

    native = postgrest_rows(postgrest, query, token=token)
    pushed = postgrest_rows(postgrest, query, parsed_filter(query), token=token)

    assert pushed == native
    assert [row["id"] for row in pushed] == [1, 4]
