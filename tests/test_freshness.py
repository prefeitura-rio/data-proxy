"""Freshness edge coverage."""

from whenever import Instant

from dp.freshness import delete_freshness, upsert_freshness
from dp.models import FullTable
from tests.helpers import FakePgConn


def test_empty_freshness_batches_do_nothing(postgres: FakePgConn) -> None:
    conn = postgres
    table = FullTable(name="p.app.t", resolved_schema="app")
    now = Instant.now()
    upsert_freshness(conn, table, set(), now, success=True)
    delete_freshness(conn, table, set())
    assert postgres.executed == []
