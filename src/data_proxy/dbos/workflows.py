"""DBOS synchronization workflows."""

from datetime import datetime

from dbos import DBOS, SetWorkflowTimeout, WorkflowHandleAsync

from ..constants import DUMP_QUEUE, REFRESH_QUEUE, publish_queue
from ..log import logger, schemaname, tablename
from ..metrics import observe
from ..models import DumpResult, DumpStatus, DumpTask, SyncPlan
from ..settings import settings
from .steps import (
    apply_ducklake_maintenance,
    build_sync_work,
    commit_ducklake_snapshot,
    commit_table_state,
    detect_published_schemas,
    extract_task,
    finalize_run,
    find_lagging_snapshots,
    list_instance_claims,
    list_serving_deployments,
    record_dump_failure,
    record_dump_metrics,
    record_publish_metrics,
    record_run_status,
    record_seed_metrics,
    restart_serving,
    run_refresh_job,
    seed_schemas,
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
) -> int | None:
    """Publish and maintain one schema, then return its new snapshot, if any."""
    schemaname.set(plan.schema_name)

    logger.info("Publish started: workflow_id=%s", run_id)

    outcome = await commit_ducklake_snapshot(plan, failed_paths)

    await record_publish_metrics(outcome, plan.schema_name)
    await commit_table_state(plan, outcome)
    logger.info(
        "Publish completed: workflow_id=%s published_tables=%d",
        run_id,
        len(outcome.published_tables),
    )

    if outcome.snapshot_id is None:
        return None

    try:
        return await apply_ducklake_maintenance(plan.schema_name)
    except Exception as error:
        logger.error(
            "DuckLake maintenance failed: workflow_id=%s error=%s", run_id, error
        )
        return outcome.snapshot_id


@DBOS.workflow()
async def refresh_catalog(schema: str, pod: str, claim: str) -> str:
    """Refresh one schema catalog on one PostgreSQL instance volume."""
    schemaname.set(schema)

    return await run_refresh_job(schema, pod, claim)


@DBOS.workflow()
async def refresh_catalogs(snapshots: dict[str, int]) -> None:
    """Refresh the published schemas on every instance until the primary reports them.

    Litestream replicates a few seconds after a commit, so a refresh that starts
    too early restores an older catalog. Only the schemas that still lag run again.
    """
    claims = await list_instance_claims()
    pending = snapshots

    for attempt in range(settings.READER_REFRESH_ATTEMPTS):
        if attempt:
            await DBOS.sleep_async(settings.READER_REFRESH_RETRY_SECONDS)

        handles = [
            await DBOS.enqueue_workflow_async(
                REFRESH_QUEUE, refresh_catalog, schema_name, pod, claim
            )
            for schema_name in pending
            for pod, claim in claims.items()
        ]

        for handle in handles:
            await handle.get_result()

        pending = await find_lagging_snapshots(pending)

        if not pending:
            return

        logger.warning(
            "Reader catalogs lag behind: attempt=%d schemas=%d",
            attempt + 1,
            len(pending),
        )

    raise RuntimeError(
        f"Reader catalogs missed snapshots after {settings.READER_REFRESH_ATTEMPTS} attempts: {sorted(pending)}"
    )


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

        publish_handles: list[tuple[str, WorkflowHandleAsync[int | None]]] = []
        for plan in work.plans:
            handle = await DBOS.enqueue_workflow_async(
                publish_queue(plan.schema_name),
                publish_schema,
                run_id,
                plan,
                failed_paths,
            )
            publish_handles.append((plan.schema_name, handle))

        results = {
            schema_name: await handle.get_result()
            for schema_name, handle in publish_handles
        }
        snapshots = await detect_published_schemas(results)

        if snapshots or postgrest_restart_required:
            deployments = await list_serving_deployments()

            if snapshots:
                await refresh_catalogs(snapshots)
                await restart_serving("PgBouncer", deployments.poolers)

            await restart_serving("PostgREST", deployments.postgrest)

        logger.info("Sync finalization started: workflow_id=%s", run_id)

        await finalize_run(run_id)

        logger.info("Sync workflow completed: workflow_id=%s", run_id)
