"""Kubernetes operations used by the synchronization workers."""

from asyncio import gather
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from operator import attrgetter
from typing import Protocol, cast

from httpx2 import AsyncClient
from kubernetes_asyncio import config
from kubernetes_asyncio.client import ApiClient, AppsV1Api

from .settings import settings
from .utils import wait_for


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
        raise RuntimeError("Deployment is not ready")


async def postgrest_api_ready(http: AsyncClient, schema: str) -> None:
    """Raise until both PostgREST Services expose their API."""
    path = "/"
    templates = (
        settings.POSTGREST_RO_SERVICE_TEMPLATE,
        settings.POSTGREST_RW_SERVICE_TEMPLATE,
    )

    for template in templates:
        service = expand_template(template, schema)
        url = (
            f"http://{service}.{settings.KUBERNETES_NAMESPACE}"
            f".svc.cluster.local:3000{path}"
        )

        try:
            response = await http.get(url, headers={"Accept-Profile": schema})
        except Exception as error:
            raise RuntimeError("PostgREST Service is unavailable") from error

        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError("PostgREST schema is not ready")


async def wait_for_postgrest_api(schema: str) -> None:
    """Wait until both PostgREST Services expose their API."""
    async with AsyncClient(timeout=5) as http:
        await wait_for(
            lambda: postgrest_api_ready(http, schema),
            timeout=settings.POSTGREST_API_TIMEOUT_SECONDS,
            interval=settings.POSTGREST_API_POLL_INTERVAL_SECONDS,
            message="PostgREST API did not expose the published schema",
        )


async def refresh_postgrest(schema: str, revision: str) -> None:
    """Restart both PostgREST deployments and wait for their rollouts."""
    load_config()

    async with api_client_factory() as api_client:
        apps = apps_factory(api_client)
        namespace = settings.KUBERNETES_NAMESPACE
        names = [
            expand_template(settings.POSTGREST_RO_DEPLOYMENT_TEMPLATE, schema),
            expand_template(settings.POSTGREST_RW_DEPLOYMENT_TEMPLATE, schema),
        ]
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {"data-proxy.io/schema-cache-revision": revision}
                    }
                }
            }
        }

        for name in names:
            await apps.patch_namespaced_deployment(
                name=name,
                namespace=namespace,
                body=patch,
            )

        async def wait_for_deployment(name: str) -> None:
            await wait_for(
                lambda: deployment_ready(
                    lambda: apps.read_namespaced_deployment(
                        name=name, namespace=namespace
                    )
                ),
                timeout=settings.POSTGREST_RO_ROLLOUT_TIMEOUT_SECONDS,
                interval=2,
                message=f"PostGREST rollout did not become ready: {name}",
            )

        await gather(*(wait_for_deployment(name) for name in names))

    await wait_for_postgrest_api(schema)
