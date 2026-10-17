/** @import { Kubernetes } from 'k6/x/kubernetes' */
/** @import { Step } from '../lib/index.js' */
import { sleep } from "k6";
import {
    checkSql,
    clusterObject,
    clusterSettled,
    info,
    killPod,
    readUncached,
    readyInstances,
    scenario,
    userToken,
    waitUntil,
} from "../lib/index.js";

const { expect, requirePrecondition } = scenario("standby");
const SAMPLES = 15;
const SAMPLE_INTERVAL_SECONDS = 2;

/**
 * A few reads can fail in the seconds before PgDog moves the reads away from the lost standby.
 */
const TOLERATED_FAILURES = 3;
const REBUILD_TIMEOUT_SECONDS = 900;

/**
 * Verifies that the loss of a standby doesn't stop the reads, and that the standby comes back.
 * @param {Kubernetes} k8s
 */
export function standbyLoss(k8s) {
    const primary = clusterObject(k8s).status?.currentPrimary;

    const standby = readyInstances(k8s)
        .map((pod) => pod.metadata.name)
        .find((name) => name !== primary);

    requirePrecondition("the cluster has a standby", standby !== undefined, { primary });
    if (standby === undefined) return;
    info("kill_pod", { pod: standby, role: "standby" });
    killPod(k8s, standby);
    let failed = 0;

    for (let sample = 0; sample < SAMPLES; sample++) {
        if (readUncached(userToken()).status !== 200) failed += 1;
        sleep(SAMPLE_INTERVAL_SECONDS);
    }

    info("reads_during_outage", { down: "standby", failed, samples: SAMPLES });
    expect("reads keep working while a standby is down", failed <= TOLERATED_FAILURES);
    expect("the primary doesn't change when a standby is lost", clusterObject(k8s).status?.currentPrimary === primary);

    expect(
        "the standby comes back and the cluster settles",
        waitUntil(
            REBUILD_TIMEOUT_SECONDS,
            () => clusterSettled(k8s),
            "the cluster to settle after the loss of a standby",
        ),
    );

    checkSql(
        k8s,
        "standby-streaming-again",
        "SELECT count(*) >= 1 FROM pg_stat_replication WHERE state = 'streaming'",
        "t",
    );

    expect("reads work after the standby returns", readUncached(userToken()).status === 200);
}

/** @type {Step[]} */
export const steps = [{ scenario: "standby", name: "standbyLoss", stage: 880, run: ({ k8s }) => standbyLoss(k8s) }];
