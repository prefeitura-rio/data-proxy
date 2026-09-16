"""Kubernetes operations used by the synchronization workers."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from operator import attrgetter
from time import monotonic
from typing import Protocol, cast

from kubernetes_asyncio import config
from kubernetes_asyncio.client import ApiClient, AppsV1Api

from .settings import settings


class DeploymentStatus(Protocol):
    """Typed Deployment status fields used by rollout checks."""

    updated_replicas: int | None
    available_replicas: int | None
    observed_generation: int | None


class DeploymentSpec(Protocol):
    """Typed Deployment spec fields used by rollout checks."""

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
        self, *, name: str, namespace: str, **_kwargs: object
    ) -> Deployment: ...

    async def read_namespaced_deployment(
        self, *, name: str, namespace: str
    ) -> Deployment: ...


class KubernetesApiClient(Protocol):
    """Typed context manager for the async Kubernetes client."""

    async def __aenter__(self) -> KubernetesApiClient: ...

    async def __aexit__(self, *_args: object) -> None: ...


load_config = cast(Callable[[], None], attrgetter("load_incluster_config")(config))
apps_factory = cast(Callable[[KubernetesApiClient], DeploymentApi], AppsV1Api)
api_client_factory = cast(
    Callable[[], AbstractAsyncContextManager[KubernetesApiClient]], ApiClient
)


async def wait_for_rollout(
    read: Callable[[], Awaitable[Deployment]], timeout: float
) -> None:
    """Wait until a Deployment has all updated replicas available."""
    deadline = monotonic() + timeout

    while monotonic() < deadline:
        deployment = await read()
        status = deployment.status
        spec = deployment.spec
        metadata = deployment.metadata

        if (
            status
            and spec
            and metadata
            and status.updated_replicas == spec.replicas
            and status.available_replicas == spec.replicas
            and status.observed_generation == metadata.generation
        ):
            return

        await asyncio.sleep(2)

    raise TimeoutError("PostGREST rollout did not become ready")


async def refresh_postgrest(schema: str, revision: str) -> None:
    """Restart both PostgREST deployments and wait for their rollouts."""
    load_config()

    async with api_client_factory() as api_client:
        apps = apps_factory(api_client)
        namespace = settings.KUBERNETES_NAMESPACE
        templates = (
            settings.POSTGREST_RO_DEPLOYMENT_TEMPLATE,
            settings.POSTGREST_RW_DEPLOYMENT_TEMPLATE,
        )

        for template in templates:
            name = template.format(schema) if "{}" in template else template

            await apps.patch_namespaced_deployment(
                name=name,
                namespace=namespace,
                body={
                    "spec": {
                        "template": {
                            "metadata": {
                                "annotations": {
                                    "data-proxy.io/schema-cache-revision": revision
                                }
                            }
                        }
                    }
                },
            )

            await wait_for_rollout(
                lambda n=name: apps.read_namespaced_deployment(
                    name=n, namespace=namespace
                ),
                timeout=settings.POSTGREST_RO_ROLLOUT_TIMEOUT_SECONDS,
            )
