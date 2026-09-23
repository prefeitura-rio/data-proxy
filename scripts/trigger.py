"""External command for triggering one DBOS synchronization workflow."""

import os
from argparse import ArgumentParser
from datetime import UTC, datetime
from typing import cast

from dbos import DBOSClient, EnqueueOptions, WorkflowHandle

from data_proxy.constants import SYNC_QUEUE


def main() -> None:
    """Enqueue one synchronization workflow and optionally wait for its result."""
    arguments = ArgumentParser()
    arguments.add_argument("--detach", action="store_true")
    command = arguments.parse_args()
    client = DBOSClient(
        system_database_url=os.environ["DBOS_SYSTEM_DATABASE_URL"],
        dbos_system_schema=os.environ.get("DBOS_SYSTEM_SCHEMA", "dbos"),
    )
    try:
        options: EnqueueOptions = {
            "workflow_name": "run_sync",
            "queue_name": SYNC_QUEUE,
        }
        handle = cast(
            "WorkflowHandle[str]", client.enqueue(options, datetime.now(UTC), {})
        )
        print(handle.workflow_id, flush=True)
        if cast(bool, command.detach):
            return
        result: str = handle.get_result()
        if result not in {"success", "no_changes"}:
            raise RuntimeError(f"Synchronization finished with status: {result}")
    finally:
        client.destroy()


if __name__ == "__main__":
    main()
