import pytest

from helpers import FakeClient, FakeQueue, request_to, send, workflow


class TestQueues:
    """The queues of the sync application."""

    async def test_lists_the_queues_by_name_with_their_limits(self) -> None:
        """Return each queue with its concurrency, rate limit and polling interval."""
        fake = FakeClient(
            queues=[
                FakeQueue("sync", global_concurrency=1),
                FakeQueue(
                    "dump",
                    worker_concurrency=4,
                    limiter={"limit": 100, "period": 60.0},
                    polling_interval_sec=0.5,
                ),
            ]
        )

        status, body = await request_to(fake, "/queues")

        assert status == 200
        assert body == [
            {
                "name": "dump",
                "global_concurrency": None,
                "worker_concurrency": 4,
                "rate_limit": {"limit": 100, "period_seconds": 60.0},
                "partitioned": False,
                "polling_interval_seconds": 0.5,
                "depth": {},
            },
            {
                "name": "sync",
                "global_concurrency": 1,
                "worker_concurrency": None,
                "rate_limit": None,
                "partitioned": False,
                "polling_interval_seconds": 1.0,
                "depth": {},
            },
        ]

    @pytest.mark.parametrize(
        "queue",
        [
            pytest.param(FakeQueue("sync", partition_concurrency=1), id="concurrency"),
            pytest.param(
                FakeQueue("sync", partition_worker_concurrency=2), id="worker"
            ),
            pytest.param(
                FakeQueue("sync", partition_limiter={"limit": 5, "period": 1.0}),
                id="limiter",
            ),
        ],
    )
    async def test_marks_a_queue_with_a_partition_limit_as_partitioned(
        self, queue: FakeQueue
    ) -> None:
        """Report a queue as partitioned when any partition limit is set."""
        _, body = await request_to(FakeClient(queues=[queue]), "/queues")

        assert body[0]["partitioned"] is True

    async def test_counts_the_waiting_and_running_workflows_by_status(self) -> None:
        """Count the workflows of each queue by status."""
        fake = FakeClient(
            queues=[FakeQueue("dump"), FakeQueue("sync")],
            queued=[
                workflow(workflow_id="d1", queue_name="dump", status="ENQUEUED"),
                workflow(workflow_id="d2", queue_name="dump", status="ENQUEUED"),
                workflow(workflow_id="d3", queue_name="dump", status="PENDING"),
                workflow(workflow_id="s1", queue_name="sync", status="PENDING"),
            ],
        )

        _, body = await request_to(fake, "/queues")

        depth = {queue["name"]: queue["depth"] for queue in body}
        assert depth == {
            "dump": {"ENQUEUED": 2, "PENDING": 1},
            "sync": {"PENDING": 1},
        }

    async def test_lists_nothing_when_no_queue_is_registered(self) -> None:
        """Return an empty list instead of an error."""
        assert await request_to(FakeClient(), "/queues") == (200, [])


class TestChangeQueue:
    """Change the limits of a queue."""

    @staticmethod
    def stored() -> FakeClient:
        """Build a client with one queue that has every kind of limit."""
        return FakeClient(
            queues=[
                FakeQueue(
                    "publish",
                    global_concurrency=4,
                    worker_concurrency=2,
                    limiter={"limit": 100, "period": 60.0},
                    partition_concurrency=1,
                    polling_interval_sec=0.5,
                )
            ]
        )

    @pytest.mark.parametrize(
        ("patch", "expected"),
        [
            pytest.param(
                {"global_concurrency": 8},
                {"global_concurrency": 8, "worker_concurrency": 2},
                id="one-limit",
            ),
            pytest.param(
                {"worker_concurrency": None},
                {"global_concurrency": 4, "worker_concurrency": None},
                id="null-clears-a-limit",
            ),
            pytest.param(
                {"rate_limit": {"limit": 10, "period_seconds": 5}},
                {"rate_limit": {"limit": 10, "period_seconds": 5.0}},
                id="rate-limit",
            ),
            pytest.param(
                {"rate_limit": None}, {"rate_limit": None}, id="no-rate-limit"
            ),
        ],
    )
    async def test_changes_only_the_limits_in_the_patch(
        self, patch: dict[str, object], expected: dict[str, object]
    ) -> None:
        """Keep every field that the patch leaves out, including the partition limits."""
        fake = self.stored()

        response = await send("PATCH", "/queues/publish", fake, json=patch)

        assert response.status_code == 200
        body = response.json()
        assert {key: body[key] for key in expected} == expected
        assert (body["partitioned"], body["polling_interval_seconds"]) == (True, 0.5)
        stored = fake.queues[0]
        assert (stored.partition_concurrency, stored.polling_interval_sec) == (1, 0.5)

    async def test_answers_404_for_an_unknown_queue(self) -> None:
        """Change nothing, and say so."""
        response = await send(
            "PATCH", "/queues/missing", FakeClient(), json={"global_concurrency": 2}
        )

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_refuses_limits_that_dbos_refuses(self) -> None:
        """Answer 422 with the reason of DBOS, and keep the stored queue."""
        fake = self.stored()

        response = await send(
            "PATCH", "/queues/publish", fake, json={"global_concurrency": 1}
        )

        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        assert "worker_concurrency" in response.json()["errors"][0]["message"]
        assert fake.queues[0].global_concurrency == 4

    async def test_refuses_a_limit_below_one_with_a_pointer(self) -> None:
        """Answer 422 as a problem document, with a pointer to the field."""
        response = await send(
            "PATCH", "/queues/publish", self.stored(), json={"global_concurrency": 0}
        )

        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        assert [item["pointer"] for item in response.json()["errors"]] == [
            "/global_concurrency"
        ]
