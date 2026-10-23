import duckdb

from sync.duckdb import limits_sql
from sync.settings import settings


def test_limits_set_the_memory_the_threads_and_the_spill_folder_of_the_settings() -> (
    None
):
    """A connection takes 80% of the memory by default, so the tasks of one pod together fill the pod."""
    reference = duckdb.connect(":memory:")
    reference.execute(f"SET memory_limit = '{settings.DUCKDB_MEMORY_LIMIT}'")
    expected_memory = reference.execute("SELECT current_setting('memory_limit')")
    memory = expected_memory.fetchone()
    reference.close()

    connection = duckdb.connect(":memory:")
    connection.execute(limits_sql())
    rows = connection.execute(
        """
        SELECT
            current_setting('memory_limit'),
            current_setting('threads'),
            current_setting('temp_directory')
        """
    ).fetchall()
    connection.close()

    assert rows == [
        (
            memory[0] if memory else None,
            settings.DUCKDB_THREADS,
            settings.DUCKDB_TEMP_DIRECTORY,
        )
    ]
