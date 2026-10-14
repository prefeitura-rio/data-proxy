"""Read the runs of the sync workflow from DBOS."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from api.models import Run, RunPage

if TYPE_CHECKING:
    from dbos import WorkflowStatus

    from api.client import WorkflowClient
    from api.models import RunStatus

SYNC_WORKFLOW = "run_sync"


def to_time(milliseconds: int | None) -> datetime | None:
    """Turn a DBOS timestamp, in milliseconds since the epoch, into a UTC time."""
    if milliseconds is None:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, UTC)


def to_run(workflow: WorkflowStatus) -> Run:
    """Describe one DBOS workflow as a run: its trigger, timing and error."""
    started = workflow.dequeued_at or workflow.created_at
    finished = workflow.completed_at
    error = workflow.error
    return Run(
        id=workflow.workflow_id,
        status=workflow.status,
        trigger="schedule" if workflow.schedule_name else "manual",
        schedule_name=workflow.schedule_name,
        queue=workflow.queue_name,
        created_at=to_time(workflow.created_at),
        started_at=to_time(workflow.dequeued_at),
        finished_at=to_time(finished),
        duration_seconds=(
            (finished - started) / 1000
            if started is not None and finished is not None
            else None
        ),
        error=None if error is None else f"{type(error).__name__}: {error}",
    )


async def list_runs(
    client: WorkflowClient,
    *,
    status: RunStatus | None,
    since: datetime | None,
    limit: int,
    offset: int,
) -> RunPage:
    """List one page of sync runs, newest first.

    DBOS gives no total, so the query asks for one row more than the page.
    """
    workflows = await client.list_workflows_async(
        name=SYNC_WORKFLOW,
        status=status,
        start_time=None if since is None else since.isoformat(),
        limit=limit + 1,
        offset=offset,
        sort_desc=True,
        load_input=False,
        load_output=False,
    )
    return RunPage(
        items=[to_run(workflow) for workflow in workflows[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(workflows) > limit,
    )
