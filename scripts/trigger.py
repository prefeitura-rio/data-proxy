"""DBOS sync workflow trigger and inspection command.

Enqueue a sync workflow, optionally wait for its result, or inspect
a previously enqueued workflow by ID.
"""

import os
from argparse import ArgumentParser
from datetime import UTC, datetime
from time import monotonic, sleep
from typing import Protocol, cast

from dbos import DBOSClient, EnqueueOptions, WorkflowHandle

from data_proxy.constants import SYNC_QUEUE

RUNNING_STATUSES = {"DELAYED", "ENQUEUED", "PENDING"}


class WorkflowView(Protocol):
    """DBOS workflow fields used by this command."""

    workflow_id: str
    status: str
    error: object
    output: object


def enqueue_workflow(client: DBOSClient, detach: bool) -> None:
    """Enqueue one synchronization workflow and optionally wait for its result."""
    options: EnqueueOptions = {
        "workflow_name": "run_sync",
        "queue_name": SYNC_QUEUE,
    }
    handle = cast("WorkflowHandle[str]", client.enqueue(options, datetime.now(UTC), {}))
    print(handle.workflow_id, flush=True)
    if detach:
        return
    result: str = handle.get_result()
    if result not in {"success", "no_changes"}:
        raise RuntimeError(f"Synchronization finished with status: {result}")


def wait_for_workflow(
    client: DBOSClient,
    workflow_id: str | None,
    timeout: float,
    expected_status: str | None = None,
) -> None:
    """Wait for a selected sync workflow and fail with its DBOS error."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        workflows = client.list_workflows(
            workflow_ids=[workflow_id] if workflow_id else None,
            name=None if workflow_id else "run_sync",
            queue_name=None if workflow_id else SYNC_QUEUE,
            limit=1,
            sort_desc=True,
            load_input=False,
            load_output=True,
        )
        if workflows:
            workflow = cast("WorkflowView", cast(object, workflows[0]))
            status = workflow.status
            if status == "SUCCESS":
                if expected_status is None or workflow.output == expected_status:
                    print(workflow.workflow_id)
                    return
                if workflow_id is not None:
                    raise RuntimeError(
                        f"Workflow {workflow.workflow_id} returned {workflow.output!r}, expected {expected_status!r}"
                    )
            if workflow_id is not None and status in {
                "ERROR",
                "CANCELLED",
                "MAX_RECOVERY_ATTEMPTS_EXCEEDED",
            }:
                raise RuntimeError(
                    f"DBOS workflow {workflow.workflow_id} failed: {workflow.error}"
                )
        sleep(2)
    raise TimeoutError("Timed out waiting for a successful DBOS sync workflow")


def wait_for_running_workflow(
    client: DBOSClient, workflow_id: str | None, timeout: float
) -> None:
    """Wait until a selected workflow is known to be non-terminal."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        workflows = client.list_workflows(
            workflow_ids=[workflow_id] if workflow_id else None,
            name=None if workflow_id else "run_sync",
            queue_name=None if workflow_id else SYNC_QUEUE,
            limit=1,
            sort_desc=True,
            load_input=False,
            load_output=False,
        )
        if workflows:
            workflow = cast("WorkflowView", cast(object, workflows[0]))
            if workflow.status in RUNNING_STATUSES:
                print(workflow.workflow_id)
                return
            if workflow_id is not None and workflow.status in {
                "SUCCESS",
                "ERROR",
                "CANCELLED",
                "MAX_RECOVERY_ATTEMPTS_EXCEEDED",
            }:
                raise RuntimeError(
                    f"DBOS workflow {workflow.workflow_id} was already terminal: {workflow.status}"
                )
        sleep(1)
    raise TimeoutError("Timed out waiting for a running sync workflow")


def main() -> None:
    """Enqueue or inspect one synchronization workflow."""
    arguments = ArgumentParser(description="Trigger or inspect a DBOS sync workflow.")
    arguments.add_argument(
        "--detach",
        action="store_true",
        help="Enqueue and return without waiting for the result.",
    )
    arguments.add_argument(
        "--workflow-id",
        help="Inspect an existing workflow by ID instead of enqueuing a new one.",
    )
    arguments.add_argument(
        "--wait",
        action="store_true",
        help="Wait for an existing workflow to reach a terminal state.",
    )
    arguments.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("DBOS_WORKFLOW_WAIT_SECONDS", "600")),
        help="Maximum seconds to wait when inspecting or waiting.",
    )

    arguments.add_argument(
        "--expect-status",
        choices=["success", "no_changes"],
        help="Require this successful workflow result when waiting.",
    )
    arguments.add_argument(
        "--expect-running",
        action="store_true",
        help="Wait until the selected workflow is non-terminal.",
    )

    options = arguments.parse_args()
    detach = cast("bool", options.detach)
    workflow_id = cast("str | None", options.workflow_id)
    wait = cast("bool", options.wait)
    timeout = cast("float", options.timeout)
    expected_status = cast("str | None", options.expect_status)
    expect_running = cast("bool", options.expect_running)
    client = DBOSClient(
        system_database_url=os.environ["DBOS_SYSTEM_DATABASE_URL"],
        dbos_system_schema=os.environ.get("DBOS_SYSTEM_SCHEMA", "dbos"),
    )

    try:
        if expect_running:
            wait_for_running_workflow(client, workflow_id, timeout)
        elif workflow_id or wait:
            wait_for_workflow(client, workflow_id, timeout, expected_status)
        else:
            enqueue_workflow(client, detach)
    finally:
        client.destroy()


if __name__ == "__main__":
    main()
