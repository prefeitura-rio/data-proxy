"""Kubernetes helpers: Deployments, instance Pods, and one-off refresh Jobs."""

import asyncio
import secrets
from contextlib import suppress
from copy import deepcopy
from math import ceil

from lightkube.core.exceptions import ApiError
from lightkube.models.batch_v1 import JobSpec
from lightkube.models.core_v1 import PersistentVolumeClaimVolumeSource
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.apps_v1 import Deployment
from lightkube.resources.batch_v1 import Job
from lightkube.resources.core_v1 import Pod
from lightkube.types import CascadeType, PatchType
from pydantic import JsonValue

from .constants import (
    CATALOG_VOLUME,
    COMPONENT_LABEL,
    INSTANCE_LABEL,
    JOB_NAME_LIMIT,
    JOB_SUFFIX_BYTES,
    NOT_FOUND,
    POSTGRES_SELECTOR,
    REFRESH_COMPONENT,
    REFRESH_JOB_TTL_SECONDS,
    RESTARTED_AT_ANNOTATION,
    SCHEMA_LABEL,
    TEMPLATE_LABEL,
)
from .log import logger
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


def is_pod_ready(pod: Pod) -> bool:
    """Return whether the Pod is not terminating and reports the Ready condition."""
    return (
        pod.metadata is not None
        and pod.metadata.deletionTimestamp is None
        and pod.status is not None
        and any(
            condition.type == "Ready" and condition.status == "True"
            for condition in pod.status.conditions or []
        )
    )


async def list_catalog_claims(
    client: KubernetesClient, namespace: str
) -> dict[str, str]:
    """Return the volume claim of every ready PostgreSQL instance Pod."""
    claims = {
        pod.metadata.name: f"{pod.metadata.name}-{CATALOG_VOLUME}"
        async for pod in client.list(
            Pod, namespace=namespace, labels={**POSTGRES_SELECTOR}
        )
        if pod.metadata is not None
        and pod.metadata.name is not None
        and is_pod_ready(pod)
    }

    return dict(sorted(claims.items()))


def build_refresh_job(
    template: JobSpec,
    namespace: str,
    schema: str,
    pod: str,
    claim: str,
    name: str,
) -> Job:
    """Copy the refresh Job template for one instance volume, without changing it."""
    spec = deepcopy(template)
    spec.selector = None
    spec.completions = 1
    spec.ttlSecondsAfterFinished = REFRESH_JOB_TTL_SECONDS

    job_labels = {
        COMPONENT_LABEL: REFRESH_COMPONENT,
        SCHEMA_LABEL: schema,
        INSTANCE_LABEL: pod,
    }
    spec.template.metadata = ObjectMeta(labels=job_labels)

    pod_spec = spec.template.spec
    volumes = (pod_spec.volumes or []) if pod_spec is not None else []

    for volume in volumes:
        if volume.name == CATALOG_VOLUME:
            volume.emptyDir = None
            volume.persistentVolumeClaim = PersistentVolumeClaimVolumeSource(
                claimName=claim
            )

    return Job(
        metadata=ObjectMeta(name=name, namespace=namespace, labels=job_labels),
        spec=spec,
    )


async def run_job(
    client: KubernetesClient, namespace: str, schema: str, pod: str, claim: str
) -> str:
    """Run the refresh Job of one schema on one instance volume and wait for it."""
    templates = [
        job
        async for job in client.list(
            Job,
            namespace=namespace,
            labels={
                COMPONENT_LABEL: REFRESH_COMPONENT,
                SCHEMA_LABEL: schema,
                TEMPLATE_LABEL: "true",
            },
        )
    ]

    if len(templates) != 1 or templates[0].spec is None:
        raise RuntimeError(
            f"Expected one refresh Job template: schema={schema} found={len(templates)}"
        )

    suffix = secrets.token_hex(JOB_SUFFIX_BYTES)
    base = f"{REFRESH_COMPONENT}-{schema}-{pod}".replace("_", "-")
    name = f"{base[: JOB_NAME_LIMIT - len(suffix) - 1].rstrip('-')}-{suffix}"

    await client.create(
        build_refresh_job(templates[0].spec, namespace, schema, pod, claim, name)
    )

    logger.info("Refresh Job started: schema=%s instance=%s", schema, pod)

    try:
        await client.wait(
            Job,
            name,
            namespace=namespace,
            for_conditions=["Complete"],
            raise_for_conditions=["Failed"],
        )
    finally:
        try:
            await client.delete(
                Job, name, namespace=namespace, cascade=CascadeType.BACKGROUND
            )
        except ApiError as error:
            if error.status.code != NOT_FOUND:
                raise

    logger.info("Refresh Job completed: schema=%s instance=%s", schema, pod)

    return name
