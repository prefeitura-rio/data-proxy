"""DBOS synchronization workflows."""

from datetime import datetime

from dbos import (
    DBOS,
    SetEnqueueOptions,
    SetWorkflowTimeout,
    WorkflowHandleAsync,
)
from dbos import error as dbos_error

from sync.constants import DUMP_QUEUE, PUBLISH_QUEUE, SYNC_QUEUE
from sync.log import SCHEMA, TABLE
from sync.metrics import observe
from sync.models import (
    DumpResult,
    DumpStatus,
    DumpTask,
    SyncConfig,
    SyncPlan,
    SyncScope,
)
from sync.schedules import is_cron, plan_schedules, schedule_name
from sync.settings import settings

from . import logs
from .steps import (
    apply_ducklake_maintenance,
    build_sync_work,
    cleanup_s3_credentials,
    commit_ducklake_snapshot,
    extract_task,
    finalize_run,
    load_sync_config,
    notify_proxies,
    reconcile_idle_schemas,
    record_dump_failure,
    record_dump_metrics,
    record_publish_metrics,
    record_run_status,
    record_scope,
    record_seed_metrics,
    reload_postgrest,
    remove_dropped_tables,
    run_provision,
    seed_schemas,
)


@DBOS.workflow()
async def dump_task(task: DumpTask) -> DumpResult:
    """Dump one task, record its result, and return it to the parent workflow."""
    SCHEMA.set(task.target_schema)
    TABLE.set(task.table)

    await logs.info("Dump started", task_id=task.task_id)

    try:
        await extract_task(task)
        result: DumpResult = DumpResult()
    except Exception as error:
        await record_dump_failure(task, str(error))
        result = DumpResult(status=DumpStatus.FAILURE, failed_paths=task.output_paths)

    await record_dump_metrics(
        task.task_id, task.table, task.target_schema, result.status.value
    )
    await logs.info(
        "Dump completed",
        task_id=task.task_id,
        status=result.status.value,
        failed_paths=len(result.failed_paths),
    )
    return result


@DBOS.workflow()
async def publish_schema(
    run_id: str, plan: SyncPlan, failed_paths: set[str], document: str
) -> int | None:
    """Publish and maintain one schema, then return its new snapshot, if any."""
    SCHEMA.set(plan.schema_name)

    await logs.info("Publish started", run_id=run_id)

    outcome = await commit_ducklake_snapshot(plan, failed_paths, document)

    await record_publish_metrics(outcome, plan.schema_name)
    await logs.info(
        "Publish completed",
        run_id=run_id,
        published_tables=len(outcome.published_tables),
    )

    if outcome.snapshot_id is None:
        return None

    try:
        return await apply_ducklake_maintenance(plan.schema_name, document)
    except Exception as error:
        await logs.error("DuckLake maintenance failed", run_id=run_id, error=str(error))
        return outcome.snapshot_id


@DBOS.workflow()
@observe(record_run_status)
async def run_sync(
    scheduled_at: datetime, context: dict[str, list[str]] | None
) -> None:
    """Build work, fan out queued children, fan in results, and finalize.

    The context is the scope of an on-demand run. A scheduled run has none and plans every table.
    """
    run_id = DBOS.workflow_id

    if run_id is None:
        raise RuntimeError("workflow_id is not set")

    await logs.info("Sync workflow started", scheduled_at=scheduled_at.isoformat())

    await run_provision()

    with SetWorkflowTimeout(settings.SYNC_RUN_TIMEOUT_SECONDS):
        loaded = await load_sync_config()
        scope = SyncScope.model_validate({} if context is None else context)
        work = await build_sync_work(run_id, loaded.document, scope)
        if len(work.plans) == 0:
            await logs.info("Sync planning completed", changes="none")
            if await reconcile_idle_schemas(loaded.document, scope):
                await reload_postgrest()
            return

        dump_handles: list[WorkflowHandleAsync[DumpResult]] = []
        for task in work.tasks:
            handle = await DBOS.enqueue_workflow_async(DUMP_QUEUE, dump_task, task)
            dump_handles.append(handle)

        await record_scope(
            run_id,
            [plan.schema_name for plan in work.plans],
            [
                (handle.workflow_id, task.target_schema, task.table)
                for handle, task in zip(dump_handles, work.tasks, strict=True)
            ],
        )

        failed_paths: set[str] = set()
        for handle in dump_handles:
            failed_paths.update((await handle.get_result()).failed_paths)

        postgrest_reload_required = await seed_schemas(work.plans, loaded.document)

        await record_seed_metrics(work.plans)

        publish_handles: list[WorkflowHandleAsync[int | None]] = []
        for plan in work.plans:
            with SetEnqueueOptions(queue_partition_key=plan.schema_name):
                handle = await DBOS.enqueue_workflow_async(
                    PUBLISH_QUEUE,
                    publish_schema,
                    run_id,
                    plan,
                    failed_paths,
                    loaded.document,
                )
            publish_handles.append(handle)

        for handle in publish_handles:
            await handle.get_result()

        if postgrest_reload_required:
            await reload_postgrest()

        await logs.info("Sync finalization started")

        await finalize_run()

        await logs.info("Sync workflow completed")


@DBOS.workflow()
async def provision_schemas() -> None:
    """Provision the schemas after a change in the console tables, and drop the S3 credentials of removed schemas."""
    await run_provision()
    loaded = await load_sync_config()
    await cleanup_s3_credentials(loaded.document)


@DBOS.workflow()
async def cleanup_tables() -> None:
    """Remove the tables that an editor deleted from the console, with their files and state."""
    loaded = await load_sync_config()
    await remove_dropped_tables(loaded.document)
    await cleanup_s3_credentials(loaded.document)


@DBOS.workflow()
async def reload_proxy() -> None:
    """Ask the proxy pods to read the configuration after a change in the console tables."""
    await notify_proxies()


@DBOS.workflow()
async def cancel_run(run_id: str) -> None:
    """Cancel a sync run, and its tasks, after a console user asked for it."""
    await DBOS.cancel_workflow_async(run_id, cancel_children=True)


@DBOS.workflow()
async def resume_run(run_id: str) -> None:
    """Put a cancelled sync run back in the sync queue to continue from its last finished step, after a console user asked for it."""
    await DBOS.resume_workflow_async(run_id, queue_name=SYNC_QUEUE)


@DBOS.workflow()
def pause_schedule(name: str) -> None:
    """Stop a schedule from firing, after a console user asked for it."""
    DBOS.pause_schedule(name)


@DBOS.workflow()
def resume_schedule(name: str) -> None:
    """Let a paused schedule fire again, after a console user asked for it."""
    DBOS.resume_schedule(name)


@DBOS.workflow()
async def reconcile_schedules() -> None:
    """Give each schema one schedule, and remove the schedule of a removed schema and the old global schedule."""
    loaded = await load_sync_config()
    schemas = SyncConfig.model_validate_json(loaded.document).schemas
    stored = await DBOS.list_schedules_async()
    plan = plan_schedules(schemas, [entry["schedule_name"] for entry in stored])

    for name in plan.delete:
        await DBOS.delete_schedule_async(name)

    for schema in plan.create:
        await DBOS.create_schedule_async(
            schedule_name=schedule_name(schema),
            workflow_fn=run_sync,
            schedule=settings.SYNC_SCHEDULE,
            context={"schemas": [schema], "tables": []},
            queue_name=SYNC_QUEUE,
        )

    await logs.info(
        "Schedules reconciled",
        schedules_created=len(plan.create),
        schedules_deleted=len(plan.delete),
    )


@DBOS.workflow()
def set_schedule(name: str, cron: str) -> None:
    """Change the cron of a schedule, after a console user asked for it.

    The schedule keeps its context, its queue and its paused state. If DBOS refuses the new cron, the old schedule comes back.
    """
    if not is_cron(cron):
        raise ValueError(f"Not a cron expression: {cron}")

    current = DBOS.get_schedule(name)

    if current is None:
        raise ValueError(f"No such schedule: {name}")

    def create(schedule: str) -> None:
        DBOS.create_schedule(
            schedule_name=name,
            workflow_fn=run_sync,
            schedule=schedule,
            context=current["context"],
            queue_name=current["queue_name"],
            cron_timezone=current["cron_timezone"],
            automatic_backfill=current["automatic_backfill"],
        )

    DBOS.delete_schedule(name)

    try:
        create(cron)
    except dbos_error.DBOSException:
        create(current["schedule"])
        raise
    finally:
        if current["status"] == "PAUSED":
            DBOS.pause_schedule(name)
