"""Read the runs of the sync workflow from DBOS."""

from collections import Counter
from typing import TYPE_CHECKING

from whenever import Instant

from console.models import ChildRun, Run, RunDetail, RunPage, Step

if TYPE_CHECKING:
    from dbos import DBOSClient, StepInfo, WorkflowStatus

    from console.models import RunFields, RunStatus

SYNC_WORKFLOW = "run_sync"


def to_time(milliseconds: int | None) -> Instant | None:
    """Turn a DBOS timestamp, in milliseconds since the epoch, into an instant."""
    if milliseconds is None:
        return None
    return Instant.from_timestamp_millis(milliseconds)


def seconds_between(started: int | None, finished: int | None) -> float | None:
    """Return the seconds from one DBOS timestamp to another, if both exist."""
    if started is None or finished is None:
        return None
    return (finished - started) / 1000


def describe_error(error: Exception | None) -> str | None:
    """Return the type and the message of an error, or None if there is no error."""
    if error is None:
        return None
    return f"{type(error).__name__}: {error}"


def run_fields(workflow: WorkflowStatus) -> RunFields:
    """Read the trigger, timing and error of a sync run from its DBOS workflow."""
    return {
        "id": workflow.workflow_id,
        "status": workflow.status,
        "trigger": "schedule" if workflow.schedule_name else "manual",
        "schedule_name": workflow.schedule_name,
        "queue": workflow.queue_name,
        "created_at": to_time(workflow.created_at),
        "started_at": to_time(workflow.dequeued_at),
        "finished_at": to_time(workflow.completed_at),
        "duration_seconds": seconds_between(
            workflow.dequeued_at or workflow.created_at, workflow.completed_at
        ),
        "error": describe_error(workflow.error),
    }


def to_child(workflow: WorkflowStatus) -> ChildRun:
    """Describe a child workflow of a run."""
    started = workflow.dequeued_at or workflow.created_at
    return ChildRun(
        id=workflow.workflow_id,
        name=workflow.name,
        status=workflow.status,
        started_at=to_time(started),
        finished_at=to_time(workflow.completed_at),
        duration_seconds=seconds_between(started, workflow.completed_at),
        error=describe_error(workflow.error),
    )


def to_step(step: StepInfo) -> Step:
    """Describe one step of a workflow."""
    started = step["started_at_epoch_ms"]
    finished = step["completed_at_epoch_ms"]
    return Step(
        id=step["function_id"],
        name=step["function_name"],
        started_at=to_time(started),
        finished_at=to_time(finished),
        duration_seconds=seconds_between(started, finished),
        child_workflow_id=step["child_workflow_id"],
        error=describe_error(step["error"]),
    )


def count_children(children: list[ChildRun]) -> dict[str, dict[str, int]]:
    """Count the child workflows by name and then by status."""
    counts: dict[str, Counter[str]] = {}
    for child in children:
        counts.setdefault(child.name, Counter())[child.status] += 1
    return {name: dict(counter) for name, counter in counts.items()}


async def list_runs(
    client: DBOSClient,
    *,
    status: RunStatus | None,
    since: Instant | None,
    limit: int,
    offset: int,
) -> RunPage:
    """List one page of sync runs, newest first.

    DBOS gives no total, so the query asks for one row more than the page.
    """
    workflows = await client.list_workflows_async(
        name=SYNC_WORKFLOW,
        status=status,
        start_time=None if since is None else since.format_iso(),
        limit=limit + 1,
        offset=offset,
        sort_desc=True,
        load_input=False,
        load_output=False,
    )
    return RunPage(
        items=[Run(**run_fields(workflow)) for workflow in workflows[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(workflows) > limit,
    )


async def get_run(client: DBOSClient, run_id: str) -> RunDetail | None:
    """Return one sync run with its steps and child workflows, or None if it is unknown."""
    runs = await client.list_workflows_async(
        name=SYNC_WORKFLOW,
        workflow_ids=[run_id],
        load_input=False,
        load_output=False,
    )
    if not runs:
        return None

    steps = await client.list_workflow_steps_async(run_id, load_output=False)
    workflows = await client.list_workflows_async(
        parent_workflow_id=run_id, load_input=False, load_output=False
    )
    children = [to_child(workflow) for workflow in workflows]
    return RunDetail(
        **run_fields(runs[0]),
        steps=[to_step(step) for step in steps],
        children=children,
        child_counts=count_children(children),
    )
