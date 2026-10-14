from asyncio import sleep

import pytest
from psycopg import AsyncConnection


@pytest.mark.postgres
class TestProxyReloadChain:
    """A change of a configuration table reaches the proxy through the queue."""

    @pytest.mark.usefixtures("sync_worker")
    async def test_a_configuration_change_makes_the_worker_reload_the_proxy(
        self, admin_db: AsyncConnection, proxy_admin: list[tuple[str, str]]
    ) -> None:
        """The trigger queues the workflow, the worker finds the pod, and the pod gets a POST."""
        await admin_db.execute(b"INSERT INTO admin.schemas (name) VALUES ('alpha')")
        await admin_db.commit()

        try:
            for _ in range(100):
                if proxy_admin:
                    break
                await sleep(0.1)

            assert proxy_admin == [("POST", "/reload")]
        finally:
            await admin_db.execute(b"DELETE FROM admin.schemas")
            await admin_db.execute(
                b"DELETE FROM dbos.workflow_status WHERE name = 'reload_proxy'"
            )
            await admin_db.commit()
