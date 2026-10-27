import { Kubernetes } from "k6/x/kubernetes";
import {
    SNAPSHOT_SOURCE,
    failedChecks,
    info,
    inStep,
    mutateSnapshotFixture,
    runCronJob,
    scenario,
    scenarioNames,
    seedAccessPolicy,
    triggerSync,
    userToken,
    warn,
    waitForAccessPolicyReplication,
    waitForJob,
    waitForPipeline,
} from "./lib/index.js";
import { contract } from "./scenarios/fixture.js";
import { STEPS } from "./scenarios/index.js";

const MAX_DURATION = __ENV.MAX_DURATION || "10m";
const PIPELINE_TIMEOUT_SECONDS = 600;

/**
 * Parses `E2E_SCENARIOS`, a comma-separated list. An empty list selects every scenario.
 * @param {string} list
 * @returns {ReadonlySet<string>}
 */
function selectedScenarios(list) {
    // A scenario that only throws, like jobs, never calls scenario(), so the steps name it too.
    const known = [...new Set([...scenarioNames(), ...STEPS.map((step) => step.scenario)])];

    const names = list
        .split(",")
        .map((name) => name.trim())
        .filter((name) => name !== "");

    const unknown = names.filter((name) => !known.includes(name));

    if (unknown.length > 0) {
        throw new Error(`unknown E2E_SCENARIOS: ${unknown.join(", ")}. Known: ${known.join(", ")}`);
    }

    return new Set(names.length === 0 ? known : names);
}

const SELECTED = selectedScenarios(__ENV.E2E_SCENARIOS ?? "");

export const options = {
    scenarios: {
        e2e: {
            executor: "shared-iterations",
            vus: 1,
            iterations: 1,
            maxDuration: MAX_DURATION,
        },
    },
    thresholds: {
        checks: ["rate==1"],
    },
    setupTimeout: "15m",
};

/**
 * Triggers a sync and waits for the sync Job to complete.
 */
export function setup() {
    const k8s = new Kubernetes();
    const jobName = triggerSync(k8s);
    waitForJob(k8s, jobName);
}

/**
 * Runs the steps of the selected scenarios against the synced fixture, in the order of their stage.
 *
 * The order is fixed: the scenarios change shared state (the snapshot, the
 * sync pods, the partition manifests, the access policy), and a later step
 * relies on what an earlier step left.
 * @param {Kubernetes} k8s
 */
function verifyEndToEnd(k8s) {
    contract();
    seedAccessPolicy();
    const completed = waitForPipeline(PIPELINE_TIMEOUT_SECONDS);

    scenario("setup").checks({
        "the sync service completed before the deadline": () => completed,
    });

    if (completed) waitForAccessPolicyReplication(userToken());

    for (const step of STEPS) {
        if (!SELECTED.has(step.scenario) || !(completed || step.needsData === false)) continue;

        try {
            inStep(step.scenario, step.name, () => step.run({ k8s }));
        } catch {
            scenario("setup").checks({
                [`the step ${step.scenario}/${step.name} ends without an exception`]: () => false,
            });
        }
    }
}

/**
 * Restores the snapshot fixture to version A and cleans the local state after the scenario.
 */
export function teardown() {
    const k8s = new Kubernetes();
    waitForJob(k8s, mutateSnapshotFixture(k8s, SNAPSHOT_SOURCE, "A"));
    waitForJob(k8s, runCronJob(k8s, "manifests-cleanup"));
}

export default function() {
    const k8s = new Kubernetes();
    info("run_started", { scenarios: SELECTED.size, steps: STEPS.length });

    try {
        verifyEndToEnd(k8s);
    } catch (error) {
        scenario("setup").checks({ "the suite ends without an exception": () => false });
        throw error;
    } finally {
        const failed = failedChecks();

        if (failed.length > 0) warn("run_failed_checks", { count: failed.length, checks: failed.join(" | ") });
        else info("run_done", { failed: 0 });
    }
}
