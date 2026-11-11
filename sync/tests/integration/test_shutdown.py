import asyncio
import os
import signal
import sys
from asyncio.subprocess import PIPE, STDOUT, Process

import pytest

from tests.constants import POSTGRES_URL

STARTED = "Sync application started"
STOPPED = "DBOS successfully shut down"
START_SECONDS = 90
STOP_SECONDS = 30


async def read_until(process: Process, text: str) -> list[str]:
    """Read the output of the worker, line by line, up to the first line that has the text."""
    assert process.stdout is not None
    lines: list[str] = []

    while True:
        line = (await process.stdout.readline()).decode()

        if len(line) == 0:
            raise AssertionError(
                f"The worker ended before it wrote {text!r}:\n{''.join(lines)}"
            )

        lines.append(line)

        if text in line:
            return lines


@pytest.mark.postgres
async def test_a_worker_that_gets_sigterm_stops_its_queues_and_exits_cleanly(
    dbos_database: str,
) -> None:
    """Kubernetes sends SIGTERM at a rollout and at a scale-down, and DBOS installs no handler of its own."""
    env = {
        **os.environ,
        "DBOS_SYSTEM_DATABASE_URL": f"{POSTGRES_URL}/{dbos_database}",
        "LISTEN_QUEUES": "control",
        "DBOS_SHUTDOWN_TIMEOUT_SECONDS": "10",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "sync.dbos.main", stdout=PIPE, stderr=STDOUT, env=env
    )

    try:
        async with asyncio.timeout(START_SECONDS):
            await read_until(process, STARTED)

        process.send_signal(signal.SIGTERM)

        async with asyncio.timeout(STOP_SECONDS):
            stopping = await read_until(process, STOPPED)
            code = await process.wait()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()

    assert code == 0, "".join(stopping)
