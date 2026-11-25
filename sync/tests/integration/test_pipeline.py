import re

import pytest

from tests.fixtures.pipeline import Pipeline, read_signature, served
from tests.helpers.core import scalar
from tests.helpers.routing import act_as_token, grant_everything

pytestmark = pytest.mark.postgres


async def test_a_run_dumps_publishes_and_serves_the_table_that_works(
    pipeline: Pipeline,
) -> None:
    """Carry the rows of the source through the dump, the DuckLake snapshot and the view."""
    await pipeline.run()
    await pipeline.pg.connection.rollback()

    assert await served(pipeline, "people") == 20
    assert await read_signature(pipeline, pipeline.good) is not None


async def test_a_table_that_fails_to_dump_is_recorded_and_left_unpublished(
    pipeline: Pipeline,
) -> None:
    """Keep one failure from the other tables: the dump error and the blocked publish are stored, and the failed table has no state."""
    await pipeline.run()
    await pipeline.pg.connection.rollback()

    assert await read_signature(pipeline, pipeline.broken) is None
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
    assert any(re.search(r"extraction exploded", str(error)) for _, error in reasons)


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


async def _read(pipeline: Pipeline, sql: str) -> list[tuple[object, ...]]:
    """Read through the view as a client of the data API with the grant (*, *)."""
    return await (await pipeline.pg.connection.execute(sql.encode())).fetchall()


async def test_a_table_with_json_columns_serves_jsonb_that_a_json_path_can_read(
    pipeline: Pipeline,
) -> None:
    """A JSON column, a struct and a list are objects and arrays in the view, and PostgREST can group, filter and sum by a path, as on a native jsonb table."""
    pipeline.only_the_documents_table()
    await pipeline.run()
    await pipeline.pg.connection.rollback()
    await act_as_token(pipeline.pg, "dp_api", pipeline.schema)
    await grant_everything(pipeline.pg)
    view = f'"{pipeline.schema}"."documents"'

    types = await _read(
        pipeline,
        f"SELECT pg_typeof(payload)::text, pg_typeof(doc)::text, pg_typeof(tags)::text, pg_typeof(amount)::text, pg_typeof(stamp)::text FROM {view} LIMIT 1",
    )
    groups = await _read(
        pipeline,
        f"SELECT payload->>'k', count(*) FROM {view} GROUP BY 1 ORDER BY 1",
    )
    total = await _read(pipeline, f"SELECT sum((payload->>'n')::int) FROM {view}")
    by_struct = await _read(
        pipeline, f"SELECT doc->>'b', count(*) FROM {view} GROUP BY 1 ORDER BY 1"
    )
    first_list = await _read(
        pipeline, f"SELECT tags->>0, count(*) FROM {view} GROUP BY 1 ORDER BY 1"
    )
    filtered = await _read(
        pipeline, f"SELECT count(*) FROM {view} WHERE payload->>'k' = 'k1'"
    )
    above = await _read(pipeline, f"SELECT count(*) FROM {view} WHERE amount > 10")
    after = await _read(
        pipeline,
        f"SELECT count(*), min(stamp)::text, max(stamp)::text FROM {view} WHERE stamp > '2026-01-01 00:00:30'",
    )

    assert types == [
        ("jsonb", "jsonb", "jsonb", "numeric", "timestamp without time zone")
    ]
    assert groups == [("k0", 20), ("k1", 20), ("k2", 20)]
    assert total == [(120,)]
    assert by_struct == [("s0", 30), ("s1", 30)]
    assert first_list == [("t0", 30), ("t1", 30)]
    assert filtered == [(20,)]
    assert above == [(19,)]
    assert after == [(29, "2026-01-01 00:00:31", "2026-01-01 00:00:59")]


async def test_a_synced_table_serves_its_generic_functions_and_loses_them_with_the_table(
    pipeline: Pipeline,
) -> None:
    """The sync writes the row of meta with the view, the wrappers read the table by its name, and the row goes with the table."""
    pipeline.only_the_documents_table()
    await pipeline.run()
    await pipeline.pg.connection.rollback()
    await act_as_token(pipeline.pg, "dp_admin", pipeline.schema)
    await grant_everything(pipeline.pg)
    schema = pipeline.schema

    meta = await _read(
        pipeline,
        f"SELECT table_name, claim_setting FROM data_proxy.meta WHERE schema_name = '{schema}'",
    )
    feed = await _read(
        pipeline,
        f"SELECT change_type, data ->> 'amount', jsonb_typeof(data -> 'payload'), jsonb_typeof(data -> 'tags') FROM \"{schema}\".changes('documents', 0) ORDER BY change_row_id LIMIT 1",
    )
    unknown = await scalar(
        pipeline.pg,
        f"SELECT count(*) FROM data_proxy.meta WHERE schema_name = '{schema}' AND table_name = 'nothing'",
    )

    assert meta == [("documents", "app.claim_sub")]
    assert feed == [("insert", "0.00", "object", "array")]
    assert unknown == 0
