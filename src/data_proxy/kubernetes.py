"""Restart the PostgREST Deployments with Lightkube."""

from typing import Protocol

from lightkube import AsyncClient
from lightkube.resources.apps_v1 import Deployment
from lightkube.types import PatchType
from pydantic import JsonValue

from .utils import wait_for


class DeploymentPatcher(Protocol):
    """The part of the Lightkube client used to patch a Deployment."""

    async def patch(
        self,
        res: type[Deployment],
        name: str,
        obj: dict[str, JsonValue],
        *,
        namespace: str | None = None,
        patch_type: PatchType = PatchType.STRATEGIC,
    ) -> Deployment: ...


async def patch_restart_annotation(
    client: DeploymentPatcher, name: str, namespace: str, restarted_at: str
) -> None:
    """Patch the Deployment pod template with the restart timestamp."""
    patch: dict[str, JsonValue] = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": restarted_at,
                    }
                }
            }
        }
    }

    await client.patch(
        Deployment,
        name,
        patch,
        namespace=namespace,
        patch_type=PatchType.STRATEGIC,
    )


async def check_deployment_rollout(
    client: AsyncClient, name: str, namespace: str
) -> None:
    """Raise until all updated Deployment replicas are available."""
    deployment = await client.get(Deployment, name, namespace=namespace)
    status = deployment.status
    spec = deployment.spec
    metadata = deployment.metadata

    if (
        status is None
        or metadata is None
        or spec.replicas is None
        or metadata.generation is None
    ):
        raise RuntimeError("Deployment not ready")

    observed = (
        status.updatedReplicas,
        status.availableReplicas,
        status.observedGeneration,
    )

    expected = (spec.replicas, spec.replicas, metadata.generation)

    if observed != expected:
        raise RuntimeError("Deployment not ready")


async def restart_deployments(
    namespace: str, names: list[str], restarted_at: str, timeout: float, kind: str
) -> None:
    """Restart every named Deployment, then wait for each rollout."""
    async with AsyncClient(namespace=namespace) as client:
        for name in names:
            await patch_restart_annotation(client, name, namespace, restarted_at)

        for name in names:
            await wait_for(
                lambda name=name: check_deployment_rollout(client, name, namespace),
                timeout=timeout,
                interval=2,
                message=f"{kind} rollout did not become ready: {name}",
            )
