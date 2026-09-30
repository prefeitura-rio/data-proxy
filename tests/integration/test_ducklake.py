"""Integration tests for the table view, change feed, and snapshots over a real DuckLake catalog."""

import psycopg
import pytest

from data_proxy.ducklake import reader_snapshot
from tests.fixtures.types import Postgres
from tests.helpers import fetch_all, response_headers, scalar, set_request_headers

pytestmark = pytest.mark.postgres


class TestTableView:
    """The table view reads the DuckLake catalog through pg_duckdb."""

    @pytest.mark.parametrize(
        ("pinned", "rows", "snapshot"),
        [
            pytest.param(None, 20, "2", id="latest-snapshot"),
            pytest.param("1", 10, "1", id="pinned-snapshot"),
        ],
    )
    async def test_reads_the_requested_snapshot(
        self, ducklake_view: Postgres, pinned: str | None, rows: int, snapshot: str
    ) -> None:
        if pinned is not None:
            await set_request_headers(ducklake_view, {"x-ducklake-snapshot": pinned})

        count = await scalar(
            ducklake_view,
            f'SELECT count(*) FROM "{ducklake_view.namespace.schema}".people',
        )

        assert count == rows
        assert await response_headers(ducklake_view) == {
            "X-Source": "ducklake",
            "X-DuckLake-Snapshot": snapshot,
        }

    async def test_rejects_a_pinned_snapshot_that_does_not_exist(
        self, ducklake_view: Postgres
    ) -> None:
        await set_request_headers(ducklake_view, {"x-ducklake-snapshot": "99"})

        with pytest.raises(psycopg.Error) as error:
            await ducklake_view.connection.execute(
                f'SELECT * FROM "{ducklake_view.namespace.schema}".people'.encode()
            )

        assert error.value.sqlstate == "PT404"


class TestChangeFeed:
    """The change-feed function returns the rows each snapshot changed."""

    @pytest.mark.parametrize(
        ("start", "end", "expected"),
        [
            pytest.param(2, None, [(2, "insert", 10)], id="since-snapshot-to-latest"),
            pytest.param(
                1, 2, [(1, "insert", 10), (2, "insert", 10)], id="snapshot-range"
            ),
        ],
    )
    async def test_returns_the_changed_rows(
        self,
        ducklake_view: Postgres,
        start: int,
        end: int | None,
        expected: list[tuple[int, str, int]],
    ) -> None:
        rows = await fetch_all(
            ducklake_view,
            "postgres/select_changes",
            mapping={"schema": ducklake_view.namespace.identifier},
            params={"start_snapshot": start, "end_snapshot": end},
        )

        assert rows == expected


class TestLatestSnapshot:
    """The reader reports the latest DuckLake snapshot."""

    async def test_snapshot_function_returns_the_latest_snapshot(
        self, ducklake_view: Postgres
    ) -> None:
        schema = ducklake_view.namespace.schema

        assert (
            await scalar(ducklake_view, f'SELECT "{schema}".ducklake_latest_snapshot()')
            == 2
        )

    async def test_reader_snapshot_reads_the_latest_snapshot(
        self, ducklake_view: Postgres
    ) -> None:
        schema = ducklake_view.namespace.schema

        assert await reader_snapshot(ducklake_view.backend, schema) == 2
