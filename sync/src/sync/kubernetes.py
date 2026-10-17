"""Run Jobs from the job template of a CronJob through the Kubernetes API."""

from asyncio import timeout
from contextlib import asynccontextmanager
from copy import deepcopy
from secrets import token_hex
from typing import TYPE_CHECKING

from asyncer import asyncify
from lightkube import AsyncClient, KubeConfig
from lightkube.models.meta_v1 import ObjectMeta, OwnerReference
from lightkube.resources.batch_v1 import CronJob, Job

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def required[T](value: T | None, name: str) -> T:
    """Return a field of the API that the CronJob must have."""
    if value is None:
        raise ValueError(f"The CronJob has no {name}")

    return value


def job_from_cronjob(cronjob: CronJob, name: str) -> Job:
    """Build the Job that `kubectl create job --from=cronjob/<name>` makes."""
    owner = required(cronjob.metadata, "metadata")
    owner_name: str | None = owner.name
    owner_uid: str | None = owner.uid
    template = cronjob.spec.jobTemplate
    metadata = deepcopy(template.metadata) or ObjectMeta()
    metadata.name = name
    metadata.annotations = {"cronjob.kubernetes.io/instantiate": "manual"}
    metadata.ownerReferences = [
        OwnerReference(
            apiVersion="batch/v1",
            kind="CronJob",
            name=required(owner_name, "name"),
            uid=required(owner_uid, "uid"),
            controller=True,
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
