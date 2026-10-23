import duckdb
import pytest

from sync.duckdb import DuckDB
from sync.settings import settings


class TestDuckDBConnect:
    """Every connection of the sync worker has its own limits."""

    @pytest.mark.asyncio
    async def test_connection_has_the_memory_threads_and_spill_folder_of_the_settings(
        self,
    ) -> None:
        """A connection takes 80% of the memory by default, so the tasks of one pod together fill the pod."""
        reference = duckdb.connect(":memory:")
        reference.execute(f"SET memory_limit = '{settings.DUCKDB_MEMORY_LIMIT}'")
        expected = reference.execute(
            "SELECT current_setting('memory_limit')"
        ).fetchone()
        reference.close()

        async with DuckDB.connect() as duckdb_conn:
            rows = await duckdb_conn.query(
                "SELECT current_setting('memory_limit'), current_setting('threads'),"
                " current_setting('temp_directory')"
            )

        assert rows == [
            (
                expected[0] if expected else None,
                settings.DUCKDB_THREADS,
                settings.DUCKDB_TEMP_DIRECTORY,
            )
        ]
