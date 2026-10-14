"""Routes that start sync runs."""

from datetime import UTC, datetime
from typing import Annotated

from dbos import WorkflowSerializationFormat
from engine.constants import SYNC_QUEUE
from engine.models import SyncConfig, SyncScope
from fastapi import APIRouter, Body, Response

from console import revisions
from console.dependencies import Client, Engine
from console.models import ErrorItem, Started
from console.problems import Problem
from console.runs import SYNC_WORKFLOW

router = APIRouter(prefix="/syncs", tags=["syncs"])


@router.post("", status_code=202)
async def start_sync(
    client: Client,
    engine: Engine,
    response: Response,
    scope: Annotated[SyncScope, Body(default_factory=SyncScope)],
) -> Started:
    """Start a sync run now, for every table or for the schemas and tables in the body.

    The run waits on the sync queue, so it never overlaps another run. The scope must name
    schemas and tables of the active config.
    """
    if scope.schemas or scope.tables:
        async with engine.connect() as connection:
            active = await revisions.active(connection)
        known = SyncConfig.model_validate(active.document if active else {})
        tables = {
            f"{table.resolved_schema}.{table.table_name}" for table in known.tables
        }
        errors = [
            ErrorItem(pointer=f"/schemas/{index}", message=f"Unknown schema {name}")
            for index, name in enumerate(scope.schemas)
            if name not in known.schemas
        ] + [
            ErrorItem(pointer=f"/tables/{index}", message=f"Unknown table {name}")
            for index, name in enumerate(scope.tables)
            if name not in tables
        ]
        if errors:
            raise Problem(422, "The scope doesn't match the active config", errors)

    handle = await client.enqueue_async(
        {
            "queue_name": SYNC_QUEUE,
            "workflow_name": SYNC_WORKFLOW,
            "serialization_type": WorkflowSerializationFormat.PORTABLE,
        },
        datetime.now(UTC),
        scope.model_dump(),
    )
    workflow_id = handle.get_workflow_id()
    response.headers["Location"] = f"/runs/{workflow_id}"
    return Started(workflow_id=workflow_id)
