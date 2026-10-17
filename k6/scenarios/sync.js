/** @import { Kubernetes } from 'k6/x/kubernetes' */
/** @import { Step } from '../lib/index.js' */
import { sleep } from "k6";
import http from "k6/http";
import {
    API_URL,
    authHeaders,
    BIG_TABLE,
    buildMetrics,
    checkSql,
    clearProxyCache,
    consoleCall,
    consoleRows,
    executeMetrics,
    FAILING_TABLE,
    FULL_TABLE,
    killWorkflowOwner,
    PARTITIONED_TABLE,
    isRecord,
    rowsOf,
    runSqlJob,
    scenario,
    snapshotValue,
    STATE_DATABASE,
    SYNCED_PARTITIONS,
    syncPods,
    TABLES,
    triggerSync,
    userToken,
    waitForJob,
    waitForPipeline,
    waitForWorkflow,
} from "../lib/index.js";

const { expect, checks } = scenario("sync");
const FAILING_SOURCE = "rj-ia-desenvolvimento.dev.failing_table";
const PHASE_TIMEOUT_SECONDS = Number(__ENV.PHASE_TIMEOUT_SECONDS || "420");

/**
 * Verifies every sync metric with a k6 check.
 */
export function metrics() {
    const metrics = buildMetrics(userToken());
    const responses = executeMetrics(metrics);

    metrics.forEach((m, i) => {
        const value = m.extract(responses[i]);

        checks({
            [m.label]: () => {
                if (m.metric.startsWith("table_status:")) return value === 200;

                if (m.metric.startsWith("table_row_count:")) {
                    return typeof value === "number" && value > 0;
                }

                if (m.metric === "partition_count:" + PARTITIONED_TABLE) {
                    return typeof value === "number" && value >= SYNCED_PARTITIONS;
                }

                return false;
            },
        });
    });
}

/**
 * Verifies that an unchanged source does not change published state.
 * @param {Kubernetes} k8s
 */
export function blockedTable(k8s) {
    const failing = `fields->>'table' = '${FAILING_SOURCE}'`;

    runSqlJob(
        k8s,
        "clear-blocked-errors",
        `DELETE FROM data_proxy.errors WHERE ${failing}`,
        "DBOS_SYSTEM_DATABASE_URL",
    );

    const snapshotBefore = snapshotValue(userToken());
    const job = triggerSync(k8s);
    waitForJob(k8s, job);

    for (const reason of ["extraction_failed", "table_blocked"]) {
        checkSql(
            k8s,
            `check-${reason}`,
            `SELECT count(*) > 0 FROM data_proxy.errors WHERE reason = '${reason}' AND ${failing}`,
            "t",
            "DBOS_SYSTEM_DATABASE_URL",
        );
    }

    const blockedErrors = rowsOf(consoleCall("GET", "/errors?reason=eq.table_blocked", "console"));

    expect(
        "a viewer sees why a table is blocked",
        blockedErrors.some((error) => isRecord(error.fields) && error.fields.table === FAILING_SOURCE),
    );

    expect(
        "a token for another schema doesn't see why a table is blocked",
        consoleRows("/errors", "admin_other") === 0,
    );

    checkSql(
        k8s,
        "check-blocked-state",
        `SELECT count(*) FILTER (WHERE table_name = '${FAILING_SOURCE}') || '/' || count(*) FROM data_proxy.state`,
        `0/${[...TABLES, BIG_TABLE].length}`,
        "DBOS_SYSTEM_DATABASE_URL",
    );

    const blocked = http.get(`${API_URL}/${FAILING_TABLE}?limit=1`, {
        headers: authHeaders(userToken()),
        tags: { name: "blocked_table" },
    });

    const served = http.get(`${API_URL}/${FULL_TABLE}?limit=1`, {
        headers: authHeaders(userToken()),
        tags: { name: "blocked_table_neighbour" },
    });

    expect("a table that failed extraction is not found", blocked.status === 404);
    expect("a table next to a failed one is still served", served.status === 200);
    expect("a failed extraction does not change the snapshot", snapshotValue(userToken()) === snapshotBefore);
}

/**
 * Kills the sync pod that owns a running workflow and verifies distributed recovery.
 *
 * A normal pod deletion lets the workflow finish during the shutdown grace
 * period, so the pod is deleted with no grace period. The recovery loop must
 * then re-enqueue the workflow for a pod that is still alive.
 * @param {Kubernetes} k8s
 */
export function recovery(k8s) {
    runSqlJob(k8s, "prepare-recovery", "DELETE FROM data_proxy.state", STATE_DATABASE);
    const podsBefore = syncPods(k8s).map((pod) => pod.metadata.uid);
    waitForJob(k8s, triggerSync(k8s, true));
    killWorkflowOwner(k8s);
    waitForWorkflow(k8s);
    const completed = waitForPipeline(PHASE_TIMEOUT_SECONDS);
    checks({ "the sync service recovered after restart": () => completed });
    const live = syncPods(k8s).map((pod) => pod.metadata.uid);
    expect("the pod that owned the workflow is gone", podsBefore.filter((uid) => !live.includes(uid)).length === 1);

    checkSql(
        k8s,
        "check-recovery-executor",
        `SELECT executor_id IN (${live.map((uid) => `'${uid}'`).join(", ")}) FROM dbos.workflow_status WHERE name = 'run_sync' AND status = 'SUCCESS' ORDER BY created_at DESC LIMIT 1`,
        "t",
        STATE_DATABASE,
    );
}

/** @type {Step[]} */
export const steps = [
    {
        scenario: "sync",
        name: "metrics",
        stage: 40,
        run: ({ k8s }) => {
            clearProxyCache(k8s);
            sleep(2);
            metrics();
        },
    },
    { scenario: "sync", name: "blockedTable", stage: 90, run: ({ k8s }) => blockedTable(k8s) },
    { scenario: "sync", name: "recovery", stage: 100, run: ({ k8s }) => recovery(k8s) },
];
