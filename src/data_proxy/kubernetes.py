"""Kubernetes operations used by the synchronization workers."""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from operator import attrgetter
from typing import Protocol, cast

from kubernetes_asyncio import config
from kubernetes_asyncio.client import ApiClient, AppsV1Api


class DeploymentStatus(Protocol):
    """Typed Deployment status fields used by rollout checks."""

    updated_replicas: int | None
    available_replicas: int | None
    observed_generation: int | None


class DeploymentSpec(Protocol):
    """Typed Deployment specification fields used by rollout checks."""

    replicas: int | None


class DeploymentMetadata(Protocol):
    """Typed Deployment metadata fields used by rollout checks."""

    generation: int | None


class Deployment(Protocol):
    """Typed Deployment response used by rollout checks."""

    status: DeploymentStatus | None
    spec: DeploymentSpec | None
    metadata: DeploymentMetadata | None


class DeploymentApi(Protocol):
    """Typed subset of the Kubernetes Deployment API used here."""

    async def patch_namespaced_deployment(
        self, *, name: str, namespace: str, **kwargs: object
    ) -> Deployment: ...

    async def read_namespaced_deployment(
        self, *, name: str, namespace: str
    ) -> Deployment: ...


class KubernetesApiClient(Protocol):
    """Typed context manager for the async Kubernetes client."""

    async def __aenter__(self) -> KubernetesApiClient: ...

    async def __aexit__(self, *args: object) -> None: ...


load_config = cast(Callable[[], None], attrgetter("load_incluster_config")(config))
apps_factory = cast(Callable[[KubernetesApiClient], DeploymentApi], AppsV1Api)
api_client_factory = cast(
    Callable[[], AbstractAsyncContextManager[KubernetesApiClient]], ApiClient
)


def expand_template(template: str, schema: str) -> str:
    """Expand a shared or per-schema Kubernetes resource template."""
    return template.format(schema) if "{}" in template else template


async def deployment_ready(read: Callable[[], Awaitable[Deployment]]) -> None:
    """Raise until a Deployment has all updated replicas available."""
    deployment = await read()
    status = deployment.status
    spec = deployment.spec
    metadata = deployment.metadata

    if not (
        status
        and spec
        and metadata
        and status.updated_replicas == spec.replicas
        and status.available_replicas == spec.replicas
        and status.observed_generation == metadata.generation
    ):
        raise RuntimeError("Deployment isn't ready")
