/** @import { Step } from '../lib/index.js' */
import { FULL_SOURCE, runOf, scenario, SCHEMA, startRun, waitForRun, waitUntil } from "../lib/index.js";

const { expect, requirePrecondition } = scenario("runs");

/**
 * The time that a run of one table needs: extraction, publication and the final checks.
 */
const RUN_TIMEOUT_SECONDS = 600;

/**
 * Verifies that an operator starts a run through the console and that the run completes.
 */
export function startSync() {
    const runId = startRun([SCHEMA], [FULL_SOURCE]);
    requirePrecondition("an operator starts a run for a schema of its token", runId !== null, {});
    if (runId === null) return;

    expect(
        "the run is listed for the operator",
        waitUntil(30, () => runOf(runId) !== undefined),
    );

    expect("the run records the name of the token that started it", runOf(runId)?.started_by === "test_admin");
    expect("the run ends with success", waitForRun(runId, RUN_TIMEOUT_SECONDS) === "SUCCESS");
    expect("the run records its tasks", Number(runOf(runId)?.task_count) > 0);
    expect("no task of the run failed", Number(runOf(runId)?.failed_task_count) === 0);
}

/** @type {Step[]} */
export const steps = [{ scenario: "runs", name: "startSync", stage: 340, run: () => startSync() }];
