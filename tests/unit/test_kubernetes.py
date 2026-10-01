"""Behavior tests for the PostgREST rollout readiness check."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from lightkube.models.apps_v1 import DeploymentStatus
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.apps_v1 import Deployment

from data_proxy import kubernetes
from data_proxy.kubernetes import check_postgrest_rollout, restart_postgrest
from tests.helpers import deployment, deployment_client, deployment_mock

READY = DeploymentStatus(updatedReplicas=2, availableReplicas=2, observedGeneration=3)
METADATA = ObjectMeta(generation=3)


class TestCheckPostgrestRollout:
    """A restart is complete only when every replica runs the new generation."""

    @pytest.mark.asyncio
    async def test_accepts_a_finished_rollout(self) -> None:
        """Pass when all replicas are updated, available, and observed."""
        await check_postgrest_rollout(
            deployment_client(status=READY, metadata=METADATA, replicas=2),
            "pgrst",
            "app",
        )

    @pytest.mark.asyncio
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
    async def test_rejects_an_unfinished_rollout(
        self,
        status: DeploymentStatus | None,
        metadata: ObjectMeta | None,
        replicas: int | None,
    ) -> None:
        """Raise until the rollout finishes so the caller keeps waiting."""
        with pytest.raises(RuntimeError, match="not ready"):
            await check_postgrest_rollout(
                deployment_client(status=status, metadata=metadata, replicas=replicas),
                "pgrst",
                "app",
            )


class TestRestartPostgrest:
    """A restart covers every PostgREST Deployment, so no read or write path keeps a stale schema."""

    @staticmethod
    def use_client(monkeypatch: pytest.MonkeyPatch, client: AsyncMock) -> None:
        """Make the module open the given client."""
        factory = MagicMock()
        factory.return_value.__aenter__.return_value = client
        monkeypatch.setattr(kubernetes, "AsyncClient", factory)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "names",
        [
            pytest.param(["pgrst"], id="single-mode"),
            pytest.param(["pgrst", "pgrst-ro"], id="ha-mode"),
        ],
    )
    async def test_restarts_every_deployment_before_waiting_for_any(
        self, monkeypatch: pytest.MonkeyPatch, names: list[str]
    ) -> None:
        """Patch all Deployments first, then wait for each rollout."""
        client = deployment_mock(status=READY, metadata=METADATA, replicas=2)
        self.use_client(monkeypatch, client)

        await restart_postgrest("app", names, "2026-01-01T00:00:00Z", 5)

        assert [call[0] for call in client.mock_calls] == [
            *["patch"] * len(names),
            *["get"] * len(names),
        ]
        assert [call.args[1] for call in client.patch.call_args_list] == names
        assert [call.args[1] for call in client.get.call_args_list] == names

    @pytest.mark.asyncio
    async def test_fails_when_one_deployment_does_not_finish(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Name the Deployment whose rollout stays unfinished."""
        done = deployment(status=READY, metadata=METADATA, replicas=2)
        stuck = deployment(status=None, metadata=METADATA, replicas=2)

        def get_deployment(_kind: type[Deployment], name: str, **_: str) -> Deployment:
            return done if name == "pgrst" else stuck

        client = deployment_mock(status=READY, metadata=METADATA, replicas=2)
        client.get.side_effect = get_deployment
        self.use_client(monkeypatch, client)

        with pytest.raises(TimeoutError, match="pgrst-ro"):
            await restart_postgrest(
                "app", ["pgrst", "pgrst-ro"], "2026-01-01T00:00:00Z", 0
            )
