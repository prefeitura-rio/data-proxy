"""External DBOS workflow inspection command for cluster validation."""

import os
from argparse import ArgumentParser
from time import monotonic, sleep
from typing import Protocol, cast

from dbos import DBOSClient


class WorkflowView(Protocol):
    """DBOS workflow fields used by this command."""

    workflow_id: str
    status: str
    error: object


def main() -> None:
    """Wait for a selected sync workflow and fail with its DBOS error."""
    arguments = ArgumentParser()
    arguments.add_argument("--workflow-id")
    options = arguments.parse_args()
    workflow_id_option = cast(str | None, options.workflow_id)
    timeout = float(os.environ.get("DBOS_WORKFLOW_WAIT_SECONDS", "600"))
    deadline = monotonic() + timeout
    client = DBOSClient(
        system_database_url=os.environ["DBOS_SYSTEM_DATABASE_URL"],
        dbos_system_schema=os.environ.get("DBOS_SYSTEM_SCHEMA", "dbos"),
    )
    try:
        while monotonic() < deadline:
            workflows = client.list_workflows(
                workflow_ids=[workflow_id_option] if workflow_id_option else None,
                name=None if workflow_id_option else "run_sync",
                queue_name=None if workflow_id_option else "sync",
                limit=1,
                sort_desc=True,
                load_input=False,
                load_output=True,
            )
            if workflows:
                workflow = cast(WorkflowView, cast(object, workflows[0]))
                workflow_id = workflow.workflow_id
                workflow_status = workflow.status
                workflow_error = workflow.error
                if workflow_status == "SUCCESS":
                    print(workflow_id)
                    return
                if workflow_status in {
                    "ERROR",
                    "CANCELLED",
                    "MAX_RECOVERY_ATTEMPTS_EXCEEDED",
                }:
                    raise RuntimeError(
                        f"DBOS workflow {workflow_id} failed: {workflow_error}"
                    )
            sleep(2)
    finally:
        client.destroy()
    raise TimeoutError("Timed out waiting for a successful DBOS sync workflow")


if __name__ == "__main__":
    main()
