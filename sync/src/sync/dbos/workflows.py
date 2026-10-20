"""DBOS synchronization workflows."""

from typing import TYPE_CHECKING

from dbos import DBOS, SetEnqueueOptions, SetWorkflowTimeout, WorkflowHandleAsync

from sync.constants import DUMP_QUEUE, PUBLISH_QUEUE, SYNC_QUEUE
from sync.log import SCHEMA, TABLE
from sync.metrics import observe
from sync.models import DumpResult, DumpStatus, DumpTask, SyncPlan, SyncScope
from sync.settings import settings

from . import logs
from .steps import (
    apply_ducklake_maintenance,
    build_sync_work,
    cleanup_s3_credentials,
    commit_ducklake_snapshot,
    commit_table_state,
    extract_task,
    finalize_run,
    load_sync_config,
    notify_proxies,
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

if TYPE_CHECKING:
    from datetime import datetime


@DBOS.workflow()
async def dump_task(task: DumpTask) -> DumpResult:
    """Dump one task, record its result, and return it to the parent workflow."""
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
    await commit_table_state(plan, outcome, document)
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
        work = await build_sync_work(
            run_id, loaded.document, SyncScope.model_validate(context or {})
        )
        if not work.plans:
            await logs.info("Sync planning completed", changes="none")
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
