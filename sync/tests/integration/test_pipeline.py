import pytest

from tests.fixtures.pipeline import Pipeline, served, state_of
from tests.helpers import scalar

pytestmark = pytest.mark.postgres


async def test_a_run_dumps_publishes_and_serves_the_table_that_works(
    pipeline: Pipeline,
) -> None:
    """Carry the rows of the source through the dump, the DuckLake snapshot and the view."""
    await pipeline.run()
    await pipeline.pg.connection.rollback()

    assert await served(pipeline, "people") == 20
    assert await state_of(pipeline, pipeline.good) is not None


async def test_a_table_that_fails_to_dump_is_recorded_and_left_unpublished(
    pipeline: Pipeline,
) -> None:
    """Keep one failure from the other tables: the dump error and the blocked publish are stored, and the failed table has no state."""
    await pipeline.run()
    await pipeline.pg.connection.rollback()

    assert await state_of(pipeline, pipeline.broken) is None
    reasons = await (
        await pipeline.pg.connection.execute(
            b"SELECT reason, fields ->> 'error' FROM data_proxy.errors WHERE fields ->> 'table' = %s",
            (pipeline.broken,),
        )
    ).fetchall()
    assert sorted(reason for reason, _ in reasons) == [
        "extraction_failed",
        "table_blocked",
    ]
    assert any("extraction exploded" in str(error) for _, error in reasons)


async def test_the_run_records_the_schema_and_each_dump_task_in_its_scope(
    pipeline: Pipeline,
) -> None:
    """Give the console what it needs to show each task only to the users of its schema."""
    await pipeline.run()
    await pipeline.pg.connection.rollback()

    rows = await (
        await pipeline.pg.connection.execute(
            b"SELECT workflow_id = run_id, table_name FROM data_proxy.scope WHERE schema_name = %s ORDER BY table_name",
            (pipeline.schema,),
        )
    ).fetchall()

    assert rows == [
        (True, ""),
        (False, pipeline.broken),
        (False, pipeline.good),
    ]


async def test_a_second_run_with_no_change_dumps_nothing_and_keeps_the_view(
    pipeline: Pipeline,
) -> None:
    """Take the idle path: no extraction, no reload of PostgREST, and the same serving objects."""
    pipeline.only_the_good_table()
    await pipeline.run()
    await pipeline.pg.connection.rollback()
    first_dumps = len(pipeline.extractions)
    before = await scalar(
        pipeline.pg,
        "SELECT oid::int FROM pg_class WHERE relname = 'people' AND relnamespace = %s::regnamespace",
        f'"{pipeline.schema}"',
    )
    pipeline.reloads.reset_mock()

    await pipeline.run()
    await pipeline.pg.connection.rollback()

    after = await scalar(
        pipeline.pg,
        "SELECT oid::int FROM pg_class WHERE relname = 'people' AND relnamespace = %s::regnamespace",
        f'"{pipeline.schema}"',
    )
    assert first_dumps == 1
    assert len(pipeline.extractions) == first_dumps
    assert before == after
    pipeline.reloads.assert_not_awaited()
