"""FastStream error handling for finite Kubernetes Jobs."""

from collections.abc import Awaitable, Callable
from typing import NoReturn

from faststream.exceptions import StopApplication

from dp.constants import DUMP_STREAM
from dp.log import logger
from dp.models import DumpFailure, DumpTask
from dp.state import complete_dump

type Publish = Callable[..., Awaitable[object]]


async def retry_or_stop(
    error: Exception,
    task: DumpTask,
    publish: Publish,
    *,
    max_retries: int,
) -> NoReturn:
    """Re-publish the task with an incremented retry count, or record the failure."""
    logger.exception("Dump failed")

    if task.retry_count < max_retries:
        await publish(
            task.model_copy(update={"retry_count": task.retry_count + 1}),
            stream=DUMP_STREAM,
        )
        raise StopApplication(1) from error

    from dp.settings import settings

    async with settings.redis as redis:
        await complete_dump(redis, task, DumpFailure(failed_path=task.bucket_path))

    raise StopApplication(1) from error


async def stop_on_error(error: Exception) -> NoReturn:
    """Log a subscriber failure, then stop the process."""
    logger.exception("Subscriber failed")
    raise StopApplication(1) from error
