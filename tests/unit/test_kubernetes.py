"""Tests for Kubernetes Deployment updates."""

from unittest.mock import AsyncMock

import pytest
from lightkube import AsyncClient
from lightkube.models.apps_v1 import DeploymentSpec, DeploymentStatus
from lightkube.models.core_v1 import PodTemplateSpec
from lightkube.models.meta_v1 import LabelSelector, ObjectMeta
from lightkube.resources.apps_v1 import Deployment
from lightkube.types import PatchType
from pydantic import JsonValue

from data_proxy.kubernetes import check_postgrest_rollout, patch_restart_annotation


def rollout_deployment(
    status: DeploymentStatus | None,
    metadata: ObjectMeta | None,
    replicas: int | None,
) -> Deployment:
    """Build one Deployment status for readiness checks."""
    return Deployment(
        metadata=metadata,
        spec=DeploymentSpec(
            selector=LabelSelector(),
            template=PodTemplateSpec(),
            replicas=replicas,
        ),
        status=status,
    )


class TestPatchRestartAnnotation:
    """Restart annotation patch behavior tests."""

    @pytest.mark.asyncio
    async def test_patches_only_the_restart_annotation(self) -> None:
        """Send the strategic merge patch with the expected payload."""

        class RecordingPatcher:
            resource: type[Deployment] | None = None
            name: str | None = None
            payload: dict[str, JsonValue] | None = None
            namespace: str | None = None
            patch_type: PatchType | None = None

            async def patch(
                self,
                res: type[Deployment],
                name: str,
                obj: dict[str, JsonValue],
                *,
                namespace: str | None = None,
                patch_type: PatchType = PatchType.STRATEGIC,
            ) -> Deployment:
                self.resource = res
                self.name = name
                self.payload = obj
                self.namespace = namespace
                self.patch_type = patch_type
                return Deployment(
                    spec=DeploymentSpec(
                        selector=LabelSelector(),
                        template=PodTemplateSpec(),
                    )
                )

        client = RecordingPatcher()
        await patch_restart_annotation(
            client, "data-proxy-postgrest", "app", "2025-01-01T00:00:00Z"
        )

        assert client.resource is Deployment
        assert client.name == "data-proxy-postgrest"
        assert client.payload == {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": "2025-01-01T00:00:00Z"
                        }
                    }
                }
            }
        }
        assert client.namespace == "app"
        assert client.patch_type == PatchType.STRATEGIC


class TestCheckPostgrestRollout:
    """PostgREST rollout readiness tests."""

    @pytest.mark.asyncio
    async def test_returns_when_deployment_is_fully_updated(self) -> None:
        """Accept matching replica counts and generation."""
        client = AsyncMock(spec=AsyncClient)
        client.get.return_value = rollout_deployment(
            DeploymentStatus(
                updatedReplicas=3,
                availableReplicas=3,
                observedGeneration=8,
            ),
            ObjectMeta(generation=8),
            3,
        )

        await check_postgrest_rollout(client, "deployment", "app")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "deployment",
        [
            pytest.param(
                rollout_deployment(None, ObjectMeta(generation=8), 3),
                id="missing-status",
            ),
            pytest.param(
                rollout_deployment(
                    DeploymentStatus(
                        updatedReplicas=3,
                        availableReplicas=3,
                        observedGeneration=8,
                    ),
                    None,
                    3,
                ),
                id="missing-metadata",
            ),
            pytest.param(
                rollout_deployment(
                    DeploymentStatus(
                        updatedReplicas=3,
                        availableReplicas=3,
                        observedGeneration=8,
                    ),
                    ObjectMeta(generation=8),
                    None,
                ),
                id="missing-desired-replicas",
            ),
            pytest.param(
                rollout_deployment(
                    DeploymentStatus(
                        updatedReplicas=3,
                        availableReplicas=3,
                        observedGeneration=8,
                    ),
                    ObjectMeta(generation=None),
                    3,
                ),
                id="missing-generation",
            ),
            pytest.param(
                rollout_deployment(
                    DeploymentStatus(
                        updatedReplicas=2,
                        availableReplicas=3,
                        observedGeneration=8,
                    ),
                    ObjectMeta(generation=8),
                    3,
                ),
                id="updated-replicas-differ",
            ),
            pytest.param(
                rollout_deployment(
                    DeploymentStatus(
                        updatedReplicas=3,
                        availableReplicas=2,
                        observedGeneration=8,
                    ),
                    ObjectMeta(generation=8),
                    3,
                ),
                id="available-replicas-differ",
            ),
            pytest.param(
                rollout_deployment(
                    DeploymentStatus(
                        updatedReplicas=3,
                        availableReplicas=3,
                        observedGeneration=7,
                    ),
                    ObjectMeta(generation=8),
                    3,
                ),
                id="generation-differs",
            ),
            pytest.param(
                rollout_deployment(
                    DeploymentStatus(
                        updatedReplicas=None,
                        availableReplicas=3,
                        observedGeneration=8,
                    ),
                    ObjectMeta(generation=8),
                    3,
                ),
                id="updated-replicas-missing",
            ),
            pytest.param(
                rollout_deployment(
                    DeploymentStatus(
                        updatedReplicas=3,
                        availableReplicas=None,
                        observedGeneration=8,
                    ),
                    ObjectMeta(generation=8),
                    3,
                ),
                id="available-replicas-missing",
            ),
            pytest.param(
                rollout_deployment(
                    DeploymentStatus(
                        updatedReplicas=3,
                        availableReplicas=3,
                        observedGeneration=None,
                    ),
                    ObjectMeta(generation=8),
                    3,
                ),
                id="observed-generation-missing",
            ),
        ],
    )
    async def test_raises_when_deployment_is_not_ready(
        self, deployment: Deployment
    ) -> None:
        """Reject missing status values and any rollout mismatch."""
        client = AsyncMock(spec=AsyncClient)
        client.get.return_value = deployment

        with pytest.raises(RuntimeError, match="Deployment not ready"):
            await check_postgrest_rollout(client, "deployment", "app")
