"""Freshness edge coverage."""

from psycopg import Connection
from whenever import Instant

from dp.freshness import delete_freshness, upsert_freshness
from dp.models import FullTable


def test_empty_freshness_batches_do_nothing(
    postgres: Connection[tuple[object, ...]],
) -> None:
    table = FullTable(name="p.app.t", resolved_schema="app")
    attempted_at = Instant.now()

    upsert_freshness(postgres, table, set(), attempted_at, success=True)
    delete_freshness(postgres, table, set())

    assert postgres.execute("SELECT 1").fetchone() == (1,)
