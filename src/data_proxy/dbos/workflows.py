import asyncio
from datetime import datetime

from dbos import DBOS, SetWorkflowTimeout, WorkflowHandleAsync

from ..constants import DUMP_QUEUE, publish_queue
from ..log import logger, schemaname, tablename
from ..metrics import RunStatus, observe_sync
from ..models import (
    DumpFailure,
    DumpResult,
    DumpSuccess,
    DumpTask,
    SyncPlan,
)
from ..settings import settings
from .steps import (
    build_sync_work,
    commit_ducklake_snapshot,
    commit_table_state,
    expire_ducklake_catalogs,
    extract_task,
    finalize_run,
    record_dump_failure,
    record_dump_metrics,
    record_publish_metrics,
    record_run_status,
    restart_postgrest,
    seed_schemas,
)


async def run_dump_tasks(tasks: list[DumpTask]) -> set[str]:
    """Run dump workflows and return errored object paths."""
    dump_handles: list[WorkflowHandleAsync[DumpResult]] = []
    for task in tasks:
        handle = await DBOS.enqueue_workflow_async(DUMP_QUEUE, dump_task, task)
        dump_handles.append(handle)

    dump_results: list[DumpResult] = await asyncio.gather(
        *(handle.get_result() for handle in dump_handles)
    )
    return {path for result in dump_results for path in result.failed_paths}


async def run_catalog_maintenance(run_id: str, plans: list[SyncPlan]) -> None:
    """Run catalog maintenance on each schema publisher queue."""
    handles = [
        await DBOS.enqueue_workflow_async(
            publish_queue(plan.schema_name), expire_catalogs, run_id, plan.schema_name
        )
        for plan in plans
    ]
    await asyncio.gather(*(handle.get_result() for handle in handles))


async def run_publish_tasks(
    run_id: str, plans: list[SyncPlan], failed_paths: set[str]
) -> None:
    """Run publication workflows for every schema plan."""
    publish_handles: list[WorkflowHandleAsync[set[str]]] = []
    for plan in plans:
        handle = await DBOS.enqueue_workflow_async(
            publish_queue(plan.schema_name),
            publish_schema,
            run_id,
            plan,
            failed_paths,
        )
        publish_handles.append(handle)

    await asyncio.gather(*(handle.get_result() for handle in publish_handles))


@DBOS.workflow()
async def dump_task(task: DumpTask) -> DumpResult:
    """Dump one task, record its result, and return it to the parent workflow."""
    tablename.set(task.table)
    logger.info("Dump started task_id=%s", task.task_id)

    try:
        await extract_task(task)
        result: DumpResult = DumpSuccess()
    except Exception as error:
        await record_dump_failure(task, str(error))
        result = DumpFailure(failed_paths=task.output_paths)

    await record_dump_metrics(
        task.task_id, task.table, task.target_schema, result.status.value
    )
    return result


@DBOS.workflow()
async def expire_catalogs(run_id: str, schema: str) -> None:
    """Expire old snapshots on one schema publisher."""
    await expire_ducklake_catalogs(schema)


@DBOS.workflow()
async def publish_schema(
    run_id: str, plan: SyncPlan, failed_paths: set[str]
) -> set[str]:
    """Publish one schema to DuckLake and commit its table state."""
    schemaname.set(plan.schema_name)

    outcome = await commit_ducklake_snapshot(plan, failed_paths)

    await record_publish_metrics(outcome, plan.schema_name)
    await commit_table_state(plan, outcome)
    return outcome.published_tables


@DBOS.workflow()
@observe_sync(record_run_status)
async def run_sync(scheduled_at: datetime, context: object) -> RunStatus:
    """Plan one run, fan out dumps, seed, fan out publishers, and finalize."""
    workflow_id = DBOS.workflow_id
    if workflow_id is None:
        raise RuntimeError("workflow_id is not set")

    with SetWorkflowTimeout(settings.SYNC_RUN_TIMEOUT_SECONDS):
        work = await build_sync_work(workflow_id)

        if not work.plans:
            logger.info("No table changes")
            return "no_changes"

        logger.info(
            "Run planned tasks=%d plans=%d",
            len(work.tasks),
            len(work.plans),
        )

        failed_paths = await run_dump_tasks(work.tasks)

        seed_result, _ = await asyncio.gather(
            seed_schemas(work.plans),
            run_publish_tasks(workflow_id, work.plans, failed_paths),
        )
        await run_catalog_maintenance(workflow_id, work.plans)

        if seed_result:
            for plan in work.plans:
                await restart_postgrest(plan.schema_name, workflow_id)

        await finalize_run(workflow_id)
        return "success"
