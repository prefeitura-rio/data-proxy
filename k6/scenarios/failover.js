/** @import { Kubernetes } from 'k6/x/kubernetes' */
/** @import { Step } from '../lib/index.js' */
import { sleep } from "k6";
import {
    changeAccessPolicy,
    clientToken,
    clusterObject,
    clusterSettled,
    FULL_SOURCE,
    FULL_TABLE,
    info,
    killPod,
    proxyGet,
    readUncached,
    scenario,
    SCHEMA,
    snapshotValue,
    startRun,
    userToken,
    waitForRun,
    waitUntil,
} from "../lib/index.js";

const { expect, requirePrecondition } = scenario("failover");

/**
 * CNPG promotes a standby in about half a minute, and rebuilds the old primary as a standby in a few minutes.
 */
const PROMOTION_TIMEOUT_SECONDS = 300;
const REBUILD_TIMEOUT_SECONDS = 900;
const RUN_TIMEOUT_SECONDS = 600;
const PROBE_ROW = [{ subject: "e2e_failover_probe", unit_type: "unit", unit_id: "unit_failover" }];

/**
 * Reads the primary that the Cluster reports.
 * @param {Kubernetes} k8s
 * @returns {string | undefined}
 */
function primaryOf(k8s) {
    return clusterObject(k8s).status?.currentPrimary;
}

/**
 * Verifies a failover of the primary: CNPG promotes the standby, the proxy serves reads again, a write and the
 * change feed work on the new primary, the published snapshot is unchanged, and a sync run completes through it.
 * @param {Kubernetes} k8s
 */
export function failover(k8s) {
    const before = primaryOf(k8s);
    requirePrecondition("the cluster has a primary", before !== undefined, {});
    requirePrecondition("the cluster is settled before the failover", clusterSettled(k8s), {});
    if (before === undefined) return;
    const snapshot = snapshotValue(userToken());
    info("kill_pod", { pod: before, role: "primary" });
    killPod(k8s, before);
    let failedReads = 0;
    const deadline = Date.now() + PROMOTION_TIMEOUT_SECONDS * 1000;

    while (Date.now() < deadline && primaryOf(k8s) === before) {
        if (readUncached(userToken()).status !== 200) failedReads += 1;
        sleep(2);
    }

    info("reads_during_outage", { down: "primary", failed: failedReads });
    const after = primaryOf(k8s);
    expect("CNPG promotes another instance to primary", after !== undefined && after !== before);

    expect(
        "the proxy serves reads again",
        waitUntil(
            PROMOTION_TIMEOUT_SECONDS,
            () => readUncached(userToken()).status === 200,
            "the proxy to serve reads after the failover",
        ),
    );

    expect(
        "the old primary rejoins as a standby and the cluster settles",
        waitUntil(REBUILD_TIMEOUT_SECONDS, () => clusterSettled(k8s), "the cluster to settle after the failover"),
    );

    expect("a policy write works on the new primary", changeAccessPolicy("POST", "", PROBE_ROW) === 201);
    expect("the policy write is removed again", changeAccessPolicy("DELETE", "?subject=eq.e2e_failover_probe") < 300);

    const feed = proxyGet(
        `/rpc/dl_changes_${FULL_TABLE}?select=change_type&start_snapshot=0&limit=1`,
        clientToken("admin"),
    );

    expect("the change feed works on the new primary", feed.status === 200);
    expect("the published snapshot is unchanged", snapshotValue(userToken()) === snapshot);
    const runId = startRun([SCHEMA], [FULL_SOURCE]);
    requirePrecondition("an operator starts a run after the failover", runId !== null, {});
    if (runId === null) return;
    expect("a run completes through the new primary", waitForRun(runId, RUN_TIMEOUT_SECONDS) === "SUCCESS");
}

/** @type {Step[]} */
export const steps = [{ scenario: "failover", name: "failover", stage: 900, run: ({ k8s }) => failover(k8s) }];
