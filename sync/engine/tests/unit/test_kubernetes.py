import pytest
from lightkube.models.apps_v1 import DeploymentStatus
from lightkube.models.meta_v1 import ObjectMeta

from engine import kubernetes
from engine.constants import POSTGREST_SELECTOR
from engine.kubernetes import (
    is_rollout_complete,
    list_deployments,
    restart_deployment,
)
from engine.types import KubernetesClient
from tests.fixtures.types import FakeKubernetes
from tests.helpers import deployment

READY = DeploymentStatus(updatedReplicas=2, availableReplicas=2, observedGeneration=3)
METADATA = ObjectMeta(generation=3)


class TestRolloutComplete:
    """A rollout is complete only when every replica runs the new generation."""

    def test_accepts_a_finished_rollout(self) -> None:
        """Accepts a finished rollout."""
        assert is_rollout_complete(
            deployment(status=READY, metadata=METADATA, replicas=2)
        )

    @pytest.mark.parametrize(
        ("status", "metadata", "replicas"),
        [
            pytest.param(
                DeploymentStatus(
                    updatedReplicas=1, availableReplicas=2, observedGeneration=3
                ),
                METADATA,
                2,
                id="replica-not-updated",
            ),
            pytest.param(
                DeploymentStatus(
                    updatedReplicas=2, availableReplicas=1, observedGeneration=3
                ),
                METADATA,
                2,
                id="replica-not-available",
            ),
            pytest.param(
                DeploymentStatus(
                    updatedReplicas=2, availableReplicas=2, observedGeneration=2
                ),
                METADATA,
                2,
                id="generation-not-observed",
            ),
            pytest.param(None, METADATA, 2, id="missing-status"),
            pytest.param(READY, None, 2, id="missing-metadata"),
            pytest.param(READY, METADATA, None, id="missing-replicas"),
        ],
    )
    def test_rejects_an_unfinished_rollout(
        self,
        status: DeploymentStatus | None,
        metadata: ObjectMeta | None,
        replicas: int | None,
    ) -> None:
        """Rejects an unfinished rollout."""
        assert not is_rollout_complete(
            deployment(status=status, metadata=metadata, replicas=replicas)
        )


class TestListDeployments:
    """Deployments are listed by label and returned in a stable order."""

    @pytest.mark.asyncio
    async def test_returns_the_sorted_names_for_the_labels(self) -> None:
        """Returns the sorted names for the labels."""
        client = FakeKubernetes(
            [
                deployment(status=None, metadata=ObjectMeta(name=name), replicas=1)
                for name in ("postgrest-ro", "postgrest")
            ]
        )

        names = await list_deployments(client, "app", POSTGREST_SELECTOR)

        assert names == ["postgrest", "postgrest-ro"]
        assert client.list_labels == [POSTGREST_SELECTOR]


class TestRestartDeployment:
    """A restart patches the Deployment before it waits for the rollout."""

    @pytest.mark.asyncio
    async def test_patches_the_deployment_before_waiting_for_its_rollout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Patches the deployment before waiting for its rollout."""
        client = FakeKubernetes()
        events: list[str] = []

        async def wait(
            _client: KubernetesClient, _namespace: str, name: str, _timeout: float
        ) -> None:
            events.append(f"wait:{name}:patched={client.patched}")

        monkeypatch.setattr(kubernetes, "wait_for_rollout", wait)

        await restart_deployment(client, "app", "a", "2026-01-01T00:00:00Z", 5)

        assert events == ["wait:a:patched=['a']"]

    @pytest.mark.asyncio
    async def test_fails_at_once_when_the_stream_ends_before_the_rollout_completes(
        self,
    ) -> None:
        """Fails at once when the stream ends before the rollout completes."""
        stuck = deployment(status=None, metadata=ObjectMeta(name="pgrst"), replicas=2)
        client = FakeKubernetes(watched=[stuck])

        with pytest.raises(TimeoutError, match="pgrst"):
            await restart_deployment(client, "app", "pgrst", "2026-01-01T00:00:00Z", 60)

    @pytest.mark.asyncio
    async def test_stops_waiting_on_a_hung_stream_when_the_timeout_expires(
        self,
    ) -> None:
        """Stops waiting on a hung stream when the timeout expires."""
        client = FakeKubernetes(hang_watch=True)

        with pytest.raises(TimeoutError, match="pgrst"):
            await restart_deployment(
                client, "app", "pgrst", "2026-01-01T00:00:00Z", 0.05
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("timeout", "expected"),
        [
            pytest.param(300, 300, id="whole-seconds"),
            pytest.param(0.05, 1, id="rounded-up"),
        ],
    )
    async def test_asks_the_server_to_end_the_stream_at_the_timeout(
        self, timeout: float, expected: int
    ) -> None:
        """Asks the server to end the stream at the timeout."""
        done = deployment(
            status=READY, metadata=ObjectMeta(name="pgrst", generation=3), replicas=2
        )
        client = FakeKubernetes(watched=[done])

        await restart_deployment(
            client, "app", "pgrst", "2026-01-01T00:00:00Z", timeout
        )

        assert client.watch_timeouts == [expected]

    @pytest.mark.asyncio
    async def test_finishes_when_the_watch_reports_a_complete_rollout(self) -> None:
        """Finishes when the watch reports a complete rollout."""
        done = deployment(
            status=READY, metadata=ObjectMeta(name="pgrst", generation=3), replicas=2
        )
        client = FakeKubernetes(watched=[done])

        await restart_deployment(client, "app", "pgrst", "2026-01-01T00:00:00Z", 5)

        assert client.patched == ["pgrst"]
