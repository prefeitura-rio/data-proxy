/** @import { Kubernetes } from 'k6/x/kubernetes' */
import {
    consoleCall,
    consoleRows,
    rowsOf,
    runOf,
    runSqlJob,
    scenario,
    STATE_DATABASE,
    waitForRun,
    waitUntil,
} from "../lib/index.js";

const { expect, requirePrecondition } = scenario("schedule");

/**
 * The DBOS scheduler reads the schedules every 30 seconds, so a firing can take a minute and a half.
 */
const FIRING_TIMEOUT_SECONDS = 180;
const RUN_TIMEOUT_SECONDS = 600;
const EVERY_MINUTE = "* * * * *";

/**
 * The runs that one schedule started, newest first.
 * @param {string} name
 * @returns {Record<string, unknown>[]}
 */
function scheduledRuns(name) {
    return rowsOf(consoleCall("GET", `/runs?schedule_name=eq.${name}&order=created_at.desc&limit=5`, "admin"));
}

/**
 * Verifies that the scheduler starts a sync run by itself. The scenario sets the cron of the schedule to every
 * minute, waits for the firing, and puts the cron back at once, so that no second run starts.
 * @param {Kubernetes} k8s
 */
export function scheduledRun(k8s) {
    const schedule = rowsOf(consoleCall("GET", "/schedules?limit=1", "admin"))[0];
    requirePrecondition("the sync has a schedule", schedule !== undefined, {});
    requirePrecondition("the schedule is active", schedule.status === "ACTIVE", { status: schedule.status });

    const name = String(schedule.name);
    const cron = String(schedule.schedule);

    const setCron = /** @param {string} value @param {string} label */ (value, label) =>
        runSqlJob(
            k8s,
            label,
            `UPDATE dbos.workflow_schedules SET schedule = '${value}' WHERE schedule_name = '${name}'`,
            STATE_DATABASE,
        );

    const known = new Set(scheduledRuns(name).map((run) => String(run.id)));
    const isNew = /** @param {Record<string, unknown>} run */ (run) => !known.has(String(run.id));

    /** @type {Record<string, unknown> | undefined} */
    let fired;
    setCron(EVERY_MINUTE, "speed-up-schedule");

    try {
        waitUntil(
            FIRING_TIMEOUT_SECONDS,
            () => {
                fired = scheduledRuns(name).find(isNew);

                return fired !== undefined;
            },
            "the scheduler to start a run",
        );
    } finally {
        setCron(cron, "restore-schedule");
    }

    expect("the scheduler starts a run after the cron changes", fired !== undefined);

    expect(
        "the cron of the schedule is back",
        waitUntil(30, () => rowsOf(consoleCall("GET", "/schedules?limit=1", "admin"))[0]?.schedule === cron),
    );

    if (fired === undefined) return;
    expect("the scheduled run carries the name of the schedule", runOf(String(fired.id))?.schedule_name === name);
    expect("the scheduled run ends with success", waitForRun(String(fired.id), RUN_TIMEOUT_SECONDS) === "SUCCESS");
    expect("the schedule is still listed for a console reader", consoleRows("/schedules", "console") > 0);
}

/** @type {Step[]} */
export const steps = [{ scenario: "schedule", name: "scheduledRun", stage: 360, run: ({ k8s }) => scheduledRun(k8s) }];
