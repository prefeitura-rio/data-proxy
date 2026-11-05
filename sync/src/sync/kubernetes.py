"""Run Jobs from the job template of a CronJob, and publish the schema list of the Swagger UI page, through the Kubernetes API."""

import json
from asyncio import timeout
from contextlib import asynccontextmanager
from copy import deepcopy
from secrets import token_hex
from typing import TYPE_CHECKING

from asyncer import asyncify
from lightkube import AsyncClient, KubeConfig
from lightkube.models.meta_v1 import ObjectMeta, OwnerReference
from lightkube.resources.batch_v1 import CronJob, Job
from lightkube.resources.core_v1 import ConfigMap

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def required[T](value: T | None, name: str) -> T:
    """Return a field of the API that the CronJob must have."""
    if value is None:
        raise ValueError(f"The CronJob has no {name}")

    return value


def job_from_cronjob(cronjob: CronJob, name: str) -> Job:
    """Build the Job of the template of a CronJob, owned by the CronJob but not controlled by it.

    The CronJob controller watches only the Jobs that it controls, and reports each active one that it didn't start as `UnexpectedJob`.
    """
    owner = required(cronjob.metadata, "metadata")
    owner_name: str | None = owner.name
    owner_uid: str | None = owner.uid
    template = cronjob.spec.jobTemplate
    copied = deepcopy(template.metadata)
    metadata = ObjectMeta() if copied is None else copied
    metadata.name = name
    metadata.annotations = {"cronjob.kubernetes.io/instantiate": "manual"}
    metadata.ownerReferences = [
        OwnerReference(
            apiVersion="batch/v1",
            kind="CronJob",
            name=required(owner_name, "name"),
            uid=required(owner_uid, "uid"),
            controller=False,
        )
    ]

    return Job(metadata=metadata, spec=deepcopy(template.spec))


@asynccontextmanager
async def k8s_client() -> AsyncGenerator[AsyncClient]:
    """Yield a client of the cluster of the pod, in the namespace of the pod."""
    client = AsyncClient(config=await asyncify(KubeConfig.from_env)())
    try:
        yield client
    finally:
        await client.close()


async def create_job(client: AsyncClient, cronjob: str) -> str:
    """Create a Job from the job template of a CronJob, and return its name."""
    name = f"{cronjob}-{token_hex(4)}"
    await client.create(job_from_cronjob(await client.get(CronJob, cronjob), name))
    return name


async def wait_for_job(client: AsyncClient, name: str, seconds: float) -> None:
    """Wait until the Job completes. Raise if it fails, or if it does not end in time."""
    try:
        async with timeout(seconds):
            await client.wait(
                Job,
                name,
                for_conditions=["Complete"],
                raise_for_conditions=["Failed"],
            )
    except TimeoutError as error:
        raise TimeoutError(
            f"The Job {name} did not end in {seconds} seconds"
        ) from error


def schemas_script(schemas: list[str]) -> str:
    """Return the script that gives the Swagger UI page its list of schemas."""
    return f"window.dataProxySchemas = {json.dumps(schemas)};\n"


async def publish_schemas(client: AsyncClient, name: str, schemas: list[str]) -> None:
    """Replace the list of schemas in the ConfigMap that the Swagger UI pod mounts."""
    await client.patch(
        ConfigMap, name, {"data": {"schemas.js": schemas_script(schemas)}}
    )
