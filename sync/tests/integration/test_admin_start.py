import asyncio

import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege

from sync.models import SyncScope
from tests.helpers import become, rows

CLAIMS = (
    "SELECT set_config('request.jwt.claims', '{\"preferred_username\": \"ana\"}', true)"
)


async def start(connection: psycopg.AsyncConnection, call: str) -> str:
    """Start a run and return its workflow id."""
    cursor = await connection.execute(f"SELECT admin.start_sync({call})".encode())
    row = await cursor.fetchone()
    assert row is not None
    identifier = row[0]
    assert isinstance(identifier, str)
    return identifier


@pytest.mark.postgres
class TestStartSync:
    """Operators start sync runs, and the worker finds them in the sync queue."""

    @pytest.mark.parametrize("role", ["console_operator", "console_admin"])
    async def test_a_started_run_waits_in_the_sync_queue_with_its_starter(
        self, admin_db: psycopg.AsyncConnection, role: str
    ) -> None:
        """The run is enqueued with portable arguments and the name from the token."""
        await admin_db.execute(CLAIMS.encode())
        await become(admin_db, role)

        identifier = await start(admin_db, "ARRAY['alpha'], ARRAY['p.d.one']")

        runs = await rows(
            admin_db,
            f"SELECT status, queue_name, started_by FROM admin.runs WHERE id = '{identifier}'",
        )
        assert runs == [("ENQUEUED", "sync", "ana")]

    @pytest.mark.parametrize("role", ["console_viewer", "console_editor"])
    async def test_lower_levels_cannot_start_a_run(
        self, admin_db: psycopg.AsyncConnection, role: str
    ) -> None:
        """Only operators and admins can start runs."""
        await become(admin_db, role)

        with pytest.raises(InsufficientPrivilege):
            await start(admin_db, "")


@pytest.mark.postgres
class TestWorkerRunsStartedSync:
    """The real worker takes a started run from the queue and plans only its scope."""

    async def test_the_worker_plans_the_scope_of_the_started_run(
        self,
        admin_db: psycopg.AsyncConnection,
        sync_worker: list[SyncScope],
    ) -> None:
        """The run succeeds, and its arguments reach the workflow as a time and a scope."""
        identifier = await start(admin_db, "ARRAY['alpha'], ARRAY['p.d.one']")
        await admin_db.commit()

        try:
            status = "ENQUEUED"
            for _ in range(100):
                result = await rows(
                    admin_db,
                    f"SELECT status FROM admin.runs WHERE id = '{identifier}'",
                )
                await admin_db.commit()
                status = str(result[0][0])
                if status not in {"ENQUEUED", "PENDING"}:
                    break
                await asyncio.sleep(0.1)

            assert (status, sync_worker) == (
                "SUCCESS",
                [SyncScope(schemas=["alpha"], tables=["p.d.one"])],
            )
        finally:
            await admin_db.execute(
                b"DELETE FROM dbos.workflow_status WHERE workflow_uuid = %s",
                (identifier,),
            )
            await admin_db.commit()
