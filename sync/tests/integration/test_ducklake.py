import psycopg
import pytest

from tests.fixtures.types import Postgres
from tests.helpers import fetch_all, response_headers, scalar, set_request_headers

pytestmark = pytest.mark.postgres

CHECK_VIEWS = """
SELECT count(*) FROM duckdb.query(
    'SELECT 1 FROM duckdb_views() WHERE view_name = ''dl_snapshot_check'''
)
"""


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
        """Reads the requested snapshot."""
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
        """Rejects a pinned snapshot that does not exist."""
        await set_request_headers(ducklake_view, {"x-ducklake-snapshot": "99"})

        with pytest.raises(psycopg.Error) as error:
            await ducklake_view.connection.execute(
                f'SELECT * FROM "{ducklake_view.namespace.schema}".people'.encode()
            )

        assert error.value.sqlstate == "PT404"

    @pytest.mark.parametrize(
        ("pinned", "checked"),
        [
            pytest.param(None, False, id="latest-snapshot-needs-no-check"),
            pytest.param("1", True, id="pinned-snapshot-is-checked"),
        ],
    )
    async def test_checks_the_snapshot_only_when_the_request_pins_it(
        self, ducklake_view: Postgres, pinned: str | None, checked: bool
    ) -> None:
        """Checks the snapshot only when the request pins it."""
        if pinned is not None:
            await set_request_headers(ducklake_view, {"x-ducklake-snapshot": pinned})
        await scalar(
            ducklake_view,
            f'SELECT count(*) FROM "{ducklake_view.namespace.schema}".people',
        )

        views = await scalar(ducklake_view, CHECK_VIEWS)

        assert views == int(checked)


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
        """Returns the changed rows."""
        rows = await fetch_all(
            ducklake_view,
            "postgres/select_changes",
            mapping={"schema": ducklake_view.namespace.identifier},
            params={"start_snapshot": start, "end_snapshot": end},
        )

        assert rows == expected


class TestTableFiles:
    """The reader lists the Parquet files that a table has at its latest snapshot."""

    async def test_lists_the_data_files_of_a_table(
        self, ducklake_view: Postgres
    ) -> None:
        """Each file has a path in the data location of the schema, and a size."""
        schema = ducklake_view.namespace.schema

        files = await fetch_all(
            ducklake_view,
            "postgres/select_table_files",
            mapping={"schema": ducklake_view.namespace.identifier},
            params={"table_name": "people"},
        )

        assert files
        assert all(
            str(path).endswith(".parquet") and "/" + schema + "/" in str(path)
            for path, _, _ in files
        )
        assert all(isinstance(size, int) and size > 0 for _, size, _ in files)

    async def test_lists_nothing_for_an_unknown_table(
        self, ducklake_view: Postgres
    ) -> None:
        """A table name that the catalog doesn't know gives no file."""
        files = await fetch_all(
            ducklake_view,
            "postgres/select_table_files",
            mapping={"schema": ducklake_view.namespace.identifier},
            params={"table_name": "nothing"},
        )

        assert files == []


class TestLatestSnapshot:
    """The reader reports the latest DuckLake snapshot."""

    async def test_snapshot_function_returns_the_latest_snapshot(
        self, ducklake_view: Postgres
    ) -> None:
        """Snapshot function returns the latest snapshot."""
        schema = ducklake_view.namespace.schema

        assert (
            await scalar(ducklake_view, f'SELECT "{schema}".dl_latest_snapshot()') == 2
        )
