"""DBOS synchronization workflows."""

from datetime import datetime  # ruff: ignore[typing-only-standard-library-import]

from dbos import DBOS, SetEnqueueOptions, SetWorkflowTimeout, WorkflowHandleAsync

from sync.constants import DUMP_QUEUE, PUBLISH_QUEUE
from sync.log import schemaname, tablename
from sync.metrics import observe
from sync.models import DumpResult, DumpStatus, DumpTask, SyncPlan, SyncScope
from sync.settings import settings

from . import logs
from .steps import (
    apply_ducklake_maintenance,
    build_sync_work,
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
    record_seed_metrics,
    reload_postgrest,
    seed_schemas,
)


@DBOS.workflow()
async def dump_task(task: DumpTask) -> DumpResult:
    """Dump one task, record its result, and return it to the parent workflow."""
    tablename.set(task.table)

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
    schemaname.set(plan.schema_name)

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
async def reload_proxy() -> None:
    """Ask the proxy pods to read the configuration after a change in the console tables."""
    await notify_proxies()
