import pytest

from helpers import FakeClient, FakeQueue, request_to, workflow


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
