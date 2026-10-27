from pathlib import Path

import duckdb

from sync.duckdb import limits_sql, spill_directory
from sync.settings import settings


def test_every_connection_spills_to_a_folder_of_its_own() -> None:
    """Connections that share a spill folder overwrite the files of each other, and DuckDB crashes with a segmentation fault."""
    folders = {spill_directory() for _ in range(50)}

    assert len(folders) == 50
    assert all(
        folder.parent == Path(settings.DUCKDB_TEMP_DIRECTORY) for folder in folders
    )


def test_limits_set_the_memory_the_threads_and_the_spill_folder_of_the_settings(
    tmp_path: Path,
) -> None:
    """A connection takes 80% of the memory by default, so the tasks of one pod together fill the pod."""
    reference = duckdb.connect(":memory:")
    reference.execute(f"SET memory_limit = '{settings.DUCKDB_MEMORY_LIMIT}'")
    expected_memory = reference.execute("SELECT current_setting('memory_limit')")
    memory = expected_memory.fetchone()
    reference.close()
    spill = tmp_path / "spill"

    connection = duckdb.connect(":memory:")
    connection.execute(limits_sql(spill))
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
            memory[0] if memory is not None and len(memory) > 0 else None,
            settings.DUCKDB_THREADS,
            str(spill),
        )
    ]
