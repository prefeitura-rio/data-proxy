"""Kubernetes helpers: Deployment lists and rollouts."""

import asyncio
from contextlib import suppress
from math import ceil
from typing import TYPE_CHECKING

from lightkube.resources.apps_v1 import Deployment
from lightkube.types import PatchType

from .constants import (
    RESTARTED_AT_ANNOTATION,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from .types import KubernetesClient


def is_rollout_complete(deployment: Deployment) -> bool:
    """Return whether every replica is updated and available for the current generation."""
    status = deployment.status
    metadata = deployment.metadata

    if (
        status is None
        or metadata is None
        or deployment.spec.replicas is None
        or metadata.generation is None
    ):
        return False

    observed = (
        status.updatedReplicas,
        status.availableReplicas,
        status.observedGeneration,
    )
    replicas = deployment.spec.replicas

    return observed == (replicas, replicas, metadata.generation)


async def list_deployments(
    client: KubernetesClient,
    namespace: str,
    labels: dict[str, str],
) -> list[str]:
    """Return the sorted names of the Deployments that match the labels."""
    names = [
        deployment.metadata.name
        async for deployment in client.list(
            Deployment, namespace=namespace, labels={**labels}
        )
        if deployment.metadata is not None and deployment.metadata.name is not None
    ]

    return sorted(names)


async def wait_for_rollout(
    client: KubernetesClient, namespace: str, name: str, timeout: float
) -> None:
    """Watch one Deployment until its rollout completes, or fail when time runs out."""
    with suppress(TimeoutError):
        async with asyncio.timeout(timeout):
            async for _, deployment in client.watch(
                Deployment,
                namespace=namespace,
                fields={"metadata.name": name},
                server_timeout=ceil(timeout),
            ):
                if is_rollout_complete(deployment):
                    return

    raise TimeoutError(f"Rollout did not become ready: {name}")


async def restart_deployment(
    client: KubernetesClient,
    namespace: str,
    name: str,
    restarted_at: str,
    timeout: float,
) -> None:
    """Restart one Deployment, then wait for its rollout."""
    patch: dict[str, JsonValue] = {
        "spec": {
            "template": {
                "metadata": {"annotations": {RESTARTED_AT_ANNOTATION: restarted_at}}
            }
        }
    }

    await client.patch(
        Deployment, name, patch, namespace=namespace, patch_type=PatchType.STRATEGIC
    )

    await wait_for_rollout(client, namespace, name, timeout)
