"""DBOS synchronization workflows."""

from datetime import datetime

from dbos import DBOS, SetWorkflowTimeout, WorkflowHandleAsync

from ..constants import DUMP_QUEUE, publish_queue
from ..log import logger, schemaname, tablename
from ..metrics import observe
from ..models import DumpResult, DumpStatus, DumpTask, SyncPlan
from ..settings import settings
from .steps import (
    build_sync_work,
    commit_ducklake_snapshot,
    commit_table_state,
    extract_task,
    finalize_run,
    record_dump_failure,
    record_dump_metrics,
    record_publish_metrics,
    record_run_status,
    record_seed_metrics,
    restart_pooler,
    restart_postgrest,
    seed_schemas,
    wait_for_reader_snapshot,
)


@DBOS.workflow()
async def dump_task(task: DumpTask) -> DumpResult:
    """Dump one task, record its result, and return it to the parent workflow."""
    tablename.set(task.table)

    logger.info("Dump started: task_id=%s", task.task_id)

    try:
        await extract_task(task)
        result: DumpResult = DumpResult()
    except Exception as error:
        await record_dump_failure(task, str(error))
        result = DumpResult(status=DumpStatus.FAILURE, failed_paths=task.output_paths)

    await record_dump_metrics(
        task.task_id, task.table, task.target_schema, result.status.value
    )
    logger.info(
        "Dump completed: task_id=%s status=%s failed_paths=%d",
        task.task_id,
        result.status.value,
        len(result.failed_paths),
    )
    return result


@DBOS.workflow()
async def publish_schema(
    run_id: str, plan: SyncPlan, failed_paths: set[str]
) -> set[str]:
    """Publish one schema to DuckLake and commit its table state."""
    schemaname.set(plan.schema_name)

    logger.info("Publish started: workflow_id=%s", run_id)

    outcome = await commit_ducklake_snapshot(plan, failed_paths)
    if outcome.snapshot_id is not None:
        await wait_for_reader_snapshot(plan.schema_name, outcome.snapshot_id)

    await record_publish_metrics(outcome, plan.schema_name)
    await commit_table_state(plan, outcome)
    logger.info(
        "Publish completed: workflow_id=%s published_tables=%d",
        run_id,
        len(outcome.published_tables),
    )
    return outcome.published_tables


@DBOS.workflow()
@observe(record_run_status)
async def run_sync(scheduled_at: datetime, context: None) -> None:
    """Build work, fan out queued children, fan in results, and finalize."""
    run_id = DBOS.workflow_id

    if run_id is None:
        raise RuntimeError("workflow_id is not set")

    logger.info(
        "Sync workflow started: workflow_id=%s scheduled_at=%s", run_id, scheduled_at
    )

    with SetWorkflowTimeout(settings.SYNC_RUN_TIMEOUT_SECONDS):
        work = await build_sync_work(run_id)
        if not work.plans:
            logger.info("Sync planning completed: changes=none")
            return

        dump_handles: list[WorkflowHandleAsync[DumpResult]] = []
        for task in work.tasks:
            handle = await DBOS.enqueue_workflow_async(DUMP_QUEUE, dump_task, task)
            dump_handles.append(handle)

        failed_paths: set[str] = set()
        for handle in dump_handles:
            failed_paths.update((await handle.get_result()).failed_paths)

        postgrest_restart_required = await seed_schemas(work.plans)

        await record_seed_metrics(work.plans)

        publish_handles: list[WorkflowHandleAsync[set[str]]] = []
        for plan in work.plans:
            handle = await DBOS.enqueue_workflow_async(
                publish_queue(plan.schema_name),
                publish_schema,
                run_id,
                plan,
                failed_paths,
            )
            publish_handles.append(handle)

        published: set[str] = set()
        for handle in publish_handles:
            published.update(await handle.get_result())

        if published:
            await restart_pooler(run_id)

        if postgrest_restart_required:
            await restart_postgrest(run_id)

        logger.info("Sync finalization started: workflow_id=%s", run_id)

        await finalize_run(run_id)

        logger.info("Sync workflow completed: workflow_id=%s", run_id)
