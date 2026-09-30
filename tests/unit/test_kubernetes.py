"""Behavior tests for the PostgREST rollout readiness check."""

import pytest
from lightkube.models.apps_v1 import DeploymentStatus
from lightkube.models.meta_v1 import ObjectMeta

from data_proxy.kubernetes import check_postgrest_rollout
from tests.helpers import deployment_client

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
