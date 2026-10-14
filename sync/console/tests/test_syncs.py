from datetime import datetime

import pytest
from dbos import WorkflowSerializationFormat
from httpx2 import AsyncClient

from helpers import FakeClient, document, send, table


class TestStartSync:
    """Start a sync run on demand."""

    async def test_starts_a_run_of_every_table_without_a_body(self) -> None:
        """Enqueue run_sync on the sync queue, answer 202, and point at the run."""
        fake = FakeClient()

        response = await send("POST", "/syncs", fake)

        assert response.status_code == 202
        assert response.json() == {"workflow_id": "run-new"}
        assert response.headers["location"] == "/runs/run-new"
        [(options, args)] = fake.enqueued
        assert options == {
            "queue_name": "sync",
            "workflow_name": "run_sync",
            "serialization_type": WorkflowSerializationFormat.PORTABLE,
        }
        assert isinstance(args[0], datetime)
        assert args[1] == {"schemas": [], "tables": []}


@pytest.mark.postgres
class TestStartSyncWithScope:
    """Start a run for some of the tables, checked against the stored config."""

    async def test_passes_a_known_scope_to_the_run(
        self, api: AsyncClient, fake: FakeClient
    ) -> None:
        """Send the schemas and tables of the request as the context of the run."""
        two = document({"a": table("proj.ds.a"), "b": table("proj.ds.b")})
        await api.put("/config", json=two, headers={"If-Match": '"0"'})

        response = await api.post(
            "/syncs", json={"schemas": ["test"], "tables": ["test.b"]}
        )

        assert response.status_code == 202
        [(_, args)] = fake.enqueued
        assert args[1] == {"schemas": ["test"], "tables": ["test.b"]}

    @pytest.mark.parametrize(
        ("scope", "pointer"),
        [
            pytest.param({"schemas": ["missing"]}, "/schemas/0", id="unknown-schema"),
            pytest.param({"tables": ["test.missing"]}, "/tables/0", id="unknown-table"),
            pytest.param(
                {"schemas": ["test", "missing"]}, "/schemas/1", id="second-schema"
            ),
        ],
    )
    async def test_refuses_a_scope_that_the_config_doesnt_know(
        self,
        api: AsyncClient,
        fake: FakeClient,
        scope: dict[str, list[str]],
        pointer: str,
    ) -> None:
        """Answer 422 with a pointer to the unknown name, and start nothing."""
        await api.put("/config", json=document(), headers={"If-Match": '"0"'})

        response = await api.post("/syncs", json=scope)

        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        assert [item["pointer"] for item in response.json()["errors"]] == [pointer]
        assert fake.enqueued == []
