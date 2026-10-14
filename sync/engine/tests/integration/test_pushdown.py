import base64
import hashlib
import hmac
import json
from typing import cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from tests.constants import POSTGREST_AUTH
from tests.fixtures.types import Postgrest


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
    request = Request(  # ruff: ignore[suspicious-url-open-usage]
        service.url + "/rpc/items" + ("?" + query if query else ""),
        headers=headers,
    )
    try:
        with urlopen(request, timeout=5) as response:  # ruff: ignore[suspicious-url-open-usage]
            body: bytes = response.read()
        rows: list[dict[str, bool | int | str]] = json.loads(body)
    except HTTPError as error:
        raise AssertionError(error.read().decode()) from error
    return rows


Node = dict[str, object]


def comparison_node(column: str, operator: str, value: str | list[str]) -> Node:
    """Build one comparison node of the filter AST."""
    return {
        "kind": "comparison",
        "column": column,
        "operator": operator,
        "negated": False,
        "value": value,
    }


def group_node(operator: str, items: list[Node], negated: bool) -> Node:
    """Build one and/or group node of the filter AST."""
    node: Node = {"kind": "group", "operator": operator, "items": items}
    if negated:
        node["negated"] = True
    return node


def filter_header(node: Node) -> str:
    """Serialize the filter document the proxy sends in X-DuckLake-Filter."""
    return json.dumps({"where": node}, separators=(",", ":"))


def render(node: Node, top: bool = False) -> str:
    """Render a node as PostgREST syntax: `col=op.val` at the top, `col.op.val` inside groups."""
    if node["kind"] == "comparison":
        value = node["value"]
        text = (
            "in.(" + ",".join(cast("list[str]", value)) + ")"
            if node["operator"] == "in"
            else f"{node['operator']}.{value}"
        )
        return f"{node['column']}{'=' if top else '.'}{text}"
    prefix = "not." if node.get("negated") else ""
    items = ",".join(render(item) for item in cast("list[Node]", node["items"]))
    return f"{prefix}{node['operator']}{'=' if top else ''}({items})"


def comparisons() -> st.SearchStrategy[Node]:
    """Return a strategy for one comparison over a known column."""
    ids = st.builds(
        comparison_node,
        st.just("id"),
        st.sampled_from(["eq", "neq", "gt", "gte", "lt", "lte"]),
        st.integers(0, 100).map(str),
    )
    names = st.builds(
        comparison_node,
        st.just("name"),
        st.sampled_from(["eq", "neq", "like", "ilike"]),
        st.sampled_from(["Rio", "Tokyo", "Paris", "rio", "*rio*", "*o*"]),
    )
    active = st.one_of(
        st.builds(
            comparison_node,
            st.just("active"),
            st.just("is"),
            st.sampled_from(["true", "false", "null", "not_null"]),
        ),
        st.builds(
            comparison_node,
            st.just("active"),
            st.just("eq"),
            st.sampled_from(["true", "false"]),
        ),
    )
    members = st.lists(
        st.integers(0, 100).map(str), min_size=1, max_size=4, unique=True
    )
    in_list = st.builds(comparison_node, st.just("id"), st.just("in"), members)
    return st.one_of(ids, names, active, in_list)


def filters() -> st.SearchStrategy[Node]:
    """Return a strategy for a filter AST: a comparison or nested and/or groups."""

    def build(left: Node, right: Node, operator: str, negated: bool) -> Node:
        return group_node(operator, [left, right], negated)

    def extend(children: st.SearchStrategy[Node]) -> st.SearchStrategy[Node]:
        return st.builds(
            build,
            children,
            children,
            st.sampled_from(["and", "or"]),
            st.booleans(),
        )

    return st.recursive(comparisons(), extend, max_leaves=4)


@pytest.mark.postgrest
@settings(max_examples=30, deadline=None)
@given(node=filters())
def test_filter_pushdown_matches_postgrest(
    postgrest: Postgrest,
    node: Node,
) -> None:
    """The pushed result matches PostgREST's native filter result."""
    query = render(node, top=True)

    native = postgrest_rows(postgrest, query)
    pushed = postgrest_rows(postgrest, query, filter_header(node))

    assert pushed == native


@pytest.mark.postgrest
def test_filter_pushdown_preserves_postgrest_rls(
    postgrest: Postgrest,
) -> None:
    """The pushed result keeps the native PostgREST row policy."""
    node = comparison_node("id", "gte", "1")
    query = render(node, top=True)
    token = token_for("alice")

    native = postgrest_rows(postgrest, query, token=token)
    pushed = postgrest_rows(postgrest, query, filter_header(node), token=token)

    assert pushed == native
    assert [row["id"] for row in pushed] == [1, 4]
