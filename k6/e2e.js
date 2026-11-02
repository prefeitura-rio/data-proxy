/**
 * The end-to-end suite. It runs the steps of every scenario in the order of their `stage` number, on the deployed
 * release. It isn't part of CI, because it needs the k3d cluster, and it's the only test of roles and access. Run it
 * before you merge a change to a permission, a role, an access policy or a view:
 *
 *   cluster k6 e2e                          validate the existing release
 *   cluster k6 e2e --build                  build and deploy fresh images first
 *   cluster k6 e2e --scenarios cache,access run a subset. A subset skips the steps in between.
 *
 * Layout: the helpers are in `k6/lib/`, split by topic. A scenario file in `k6/scenarios/` exports `steps`, a list of
 * `{ scenario, name, stage, run }`. The stage numbers leave gaps, so a new step fits between two others. To add a
 * scenario, create the file and add one import and one `...<name>.steps` line to `k6/scenarios/index.js`. The order is
 * fixed, because the steps change the shared state (the snapshot, the sync pods, the access policy). `failover` runs
 * last, because it kills the primary.
 *
 * Checks: a check is named `scenario: description` and asserts one condition. A step that throws doesn't stop the
 * run. The orchestration records a failed check for it and goes on, so read the first failure first.
 *
 * Needs: the Keycloak test realm has one client for each role (`api`, `api_no_policy`, `policy`, `console`, `admin`,
 * `admin_other`). The scenarios `tokens`, `idp` and `roles` need extra clients, and `newschema` needs the table
 * `new_schema_table`, which `seed` creates. Keycloak imports its realm at the first start only, so run `cluster down`
 * and `cluster up` after a change of `k3d/values/keycloak.yaml`.
 *
 * Scenarios: `schedule` and `runs` (a run starts and completes), `newschema` (a new schema is served and removed
 * end to end), `tokens` and `idp` (the gateway refuses a bad token, and an identity-provider user reads), `roles` (one
 * probe for each role and permission, which follows `rbac.toml`), `console` (the console API at every level),
 * `standby`, `cachestore` and `failover` (the system survives a loss), `metrics`, `jobs` (a backup restores into a
 * scratch schema) and `cache` (a write that skips the proxy goes stale after a reload).
 *
 * Log: every line has the shape `scenario=<name> step=<step> event=<event>` with more `key=value` fields. Events:
 * `run_*`, `step_*`, `check_failed` (with the last request), `wait_*`, `job_*`, `kill_pod` and
 * `reads_during_outage`. Follow a run with `rg 'event=(step_|check_failed|wait_timed_out|job_failed)'`.
 *
 * The k6 files are plain JavaScript with JSDoc types, which `k6/tsconfig.json` checks. `Containerfile.k6` copies `k6/`
 * into the image, and the `cluster k6` commands build and push it with a new tag before each run.
 */
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
 * Parses `E2E_SCENARIOS`, a comma-separated list. An empty list selects every scenario. A scenario that only throws,
 * like jobs, never calls scenario(), so the steps name it too.
 * @param {string} list
 * @returns {ReadonlySet<string>}
 */
function selectedScenarios(list) {
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
