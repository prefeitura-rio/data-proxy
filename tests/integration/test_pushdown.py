"""Differential filter checks against a real PostgREST service."""

import base64
import hashlib
import hmac
import json
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

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


@pytest.mark.postgrest
@pytest.mark.parametrize(
    "query",
    [
        "id=eq.2",
        "or=(id.eq.1,name.ilike.*rio*)",
        "active=is.true",
        "active=is.not_null",
        "name=not.ilike.*yo*",
        "not.and=(id.gte.1,id.lte.4)",
        "id=in.(1,4)&order=id.desc&limit=1&offset=0",
    ],
)
def test_filter_pushdown_matches_postgrest(
    postgrest: Postgrest,
    query: str,
) -> None:
    """The pushed result matches PostgREST's native filter result."""
    native = postgrest_rows(postgrest, query)
    pushed = postgrest_rows(postgrest, "", parsed_filter(query))

    assert pushed == native


@pytest.mark.postgrest
def test_filter_pushdown_preserves_postgrest_rls(
    postgrest: Postgrest,
) -> None:
    """The pushed result keeps the native PostgREST row policy."""
    query = "id=gte.1"
    token = token_for("alice")

    native = postgrest_rows(postgrest, query, token=token)
    pushed = postgrest_rows(postgrest, "", parsed_filter(query), token=token)

    assert pushed == native
    assert [row["id"] for row in pushed] == [1, 4]
