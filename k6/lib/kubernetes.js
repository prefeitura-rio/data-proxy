/** @import { Kubernetes, KubernetesPod, KubernetesPodSpec } from 'k6/x/kubernetes' */
import { sleep } from "k6";
import {
    CONFIGURE_DB_CRONJOB,
    E2E_SCRIPT_CONFIGMAP,
    MAINTENANCE_CRONJOB,
    NAMESPACE,
    PG_IMAGE,
    PIPELINE,
    STATE_DATABASE,
} from "./env.js";
import { heartbeat, info, warn } from "./log.js";

/**
 * Returns the sync pod spec, used as a template for one-off Jobs.
 * @param {Kubernetes} k8s
 * @param {string} deploymentName
 * @returns {KubernetesPodSpec}
 */
function deploymentPodSpec(k8s, deploymentName) {
    const deployment = k8s.get("Deployment.apps", deploymentName, NAMESPACE);

    return deployment.spec.template.spec;
}

/**
 * @param {Kubernetes} k8s
 * @returns {KubernetesPodSpec}
 */
export function workerPodSpec(k8s) {
    return deploymentPodSpec(k8s, PIPELINE);
}

/**
 * Returns the pod spec of a CronJob, used as a template for one-off Jobs.
 * @param {Kubernetes} k8s
 * @param {string} cronJobName
 * @returns {KubernetesPodSpec}
 */
export function cronJobPodSpec(k8s, cronJobName) {
    const cronJob = k8s.get("CronJob.batch", cronJobName, NAMESPACE);

    return cronJob.spec.jobTemplate.spec.template.spec;
}

/**
 * Adds the standalone e2e scripts to a sync pod spec.
 * @param {KubernetesPodSpec} podSpec
 * @returns {KubernetesPodSpec}
 */
function scriptPodSpec(podSpec) {
    const scriptVolume = {
        name: "e2e-scripts",
        configMap: { name: E2E_SCRIPT_CONFIGMAP },
    };

    const scriptMount = {
        name: "e2e-scripts",
        mountPath: "/scripts",
        readOnly: true,
    };

    return {
        ...podSpec,
        volumes: [...(podSpec.volumes || []), scriptVolume],
        containers: podSpec.containers.map((container) => ({
            ...container,
            volumeMounts: [
                ...(container.volumeMounts || []).filter((mount) => mount.mountPath !== scriptMount.mountPath),
                scriptMount,
            ],
        })),
    };
}

/**
 * Creates one Job with the given spec and returns its name. Every Job of the suite is created here.
 * @param {Kubernetes} k8s
 * @param {string} name
 * @param {object} spec
 * @returns {string}
 */
export function createJob(k8s, name, spec) {
    k8s.create({
        apiVersion: "batch/v1",
        kind: "Job",
        metadata: { name, namespace: NAMESPACE },
        spec: { backoffLimit: 0, ...spec },
    });

    return name;
}

/**
 * Builds the Job spec that runs one container in the pod of a template.
 * @param {PodJob} job
 * @returns {object}
 */
function podJobSpec(job) {
    const first = job.podSpec.containers[0];
    const base = job.image === undefined ? first : { volumeMounts: first.volumeMounts };

    return {
        ...(job.ttlSecondsAfterFinished === undefined ? {} : { ttlSecondsAfterFinished: job.ttlSecondsAfterFinished }),
        template: {
            spec: {
                ...job.podSpec,
                ...(job.serviceAccountName === undefined ? {} : { serviceAccountName: job.serviceAccountName }),
                restartPolicy: "Never",
                containers: [
                    {
                        ...base,
                        name: job.container,
                        ...(job.image === undefined ? {} : { image: job.image }),
                        command: job.command,
                        ...(job.args === undefined ? {} : { args: job.args }),
                        env: [...(first.env || []), ...(job.env || [])],
                    },
                ],
            },
        },
    };
}

/**
 * Creates a Job that runs one of the e2e scripts in the pod of the sync workers.
 * @param {Kubernetes} k8s
 * @param {string} prefix
 * @param {string} container
 * @param {string[]} command
 * @param {PodJob["env"]} [env]
 * @returns {string}
 */
function scriptJob(k8s, prefix, container, command, env) {
    const spec = podJobSpec({
        container,
        command,
        env,
        ttlSecondsAfterFinished: 300,
        podSpec: scriptPodSpec(workerPodSpec(k8s)),
    });

    return createJob(k8s, `${prefix}-${Date.now()}`, spec);
}

/**
 * Creates a Job that sets the fixture of the snapshot scenario to one version.
 * @param {Kubernetes} k8s
 * @param {string} source
 * @param {string} version
 * @returns {string}
 */
export function mutateSnapshotFixture(k8s, source, version) {
    return scriptJob(
        k8s,
        "data-proxy-mutate-snapshot",
        "mutate-snapshot",
        ["python", "/scripts/mutate.py", "--version", version],
        [{ name: "SNAPSHOT_SOURCE", value: source }],
    );
}

/**
 * Creates a Job that triggers the DBOS sync schedule once. Detached, the Job doesn't wait for the run.
 * @param {Kubernetes} k8s
 * @param {boolean} [detached=false]
 * @returns {string}
 */
export function triggerSync(k8s, detached = false) {
    return scriptJob(k8s, "data-proxy-sync-k6", "trigger", [
        "python",
        "/scripts/trigger.py",
        ...(detached ? ["--detach"] : []),
    ]);
}

/**
 * Lists the sync pods. DBOS uses each pod UID as its executor ID.
 * @param {Kubernetes} k8s
 * @returns {KubernetesPod[]}
 */
export function syncPods(k8s) {
    return k8s.list("Pod", NAMESPACE).filter((pod) => pod.metadata.labels?.["app.kubernetes.io/component"] === "sync");
}

/**
 * Waits for a Kubernetes Job to finish and returns how it ended.
 * @param {Kubernetes} k8s
 * @param {string} name
 * @returns {"succeeded" | "failed"}
 */
function jobResult(k8s, name) {
    const deadline = Date.now() + 900_000;
    const beat = heartbeat("job_waiting", { job: name });

    while (Date.now() < deadline) {
        beat();
        const job = k8s.get("Job.batch", name, NAMESPACE);
        if (job.status?.succeeded && job.status.succeeded > 0) return "succeeded";
        if (job.status?.failed && job.status.failed > 0) return "failed";
        sleep(1);
    }

    throw new Error(`Job ${name} timed out`);
}

/**
 * Waits for a Kubernetes Job to succeed and fails when it fails.
 * @param {Kubernetes} k8s
 * @param {string} name
 */
export function waitForJob(k8s, name) {
    const started = Date.now();
    info("job_started", { job: name });
    const result = jobResult(k8s, name);
    const seconds = Math.round((Date.now() - started) / 1000);

    if (result === "failed") {
        warn("job_failed", { job: name, seconds });
        throw new Error(`Job ${name} failed`);
    }

    info("job_done", { job: name, seconds });
}

/**
 * Creates a Job that waits for the latest DBOS sync workflow to complete after a detached trigger.
 * @param {Kubernetes} k8s
 */
export function waitForWorkflow(k8s) {
    waitForJob(k8s, scriptJob(k8s, "data-proxy-workflow-k6", "wait", ["python", "/scripts/trigger.py", "--wait"]));
}

/**
 * Starts one Job from a CronJob template, as `kubectl create job --from` does.
 * @param {Kubernetes} k8s
 * @param {string} cronJobName
 * @returns {string}
 */
export function runCronJob(k8s, cronJobName) {
    const cronJob = k8s.get("CronJob.batch", cronJobName, NAMESPACE);

    return createJob(k8s, `${cronJobName}-e2e-${Date.now()}`, cronJob.spec.jobTemplate.spec);
}

/**
 * Shell prefix that points rclone at an object store. The credentials come from `AWS_*` in the pod, and the
 * endpoint is a shell word: the backup pod has a full URL in `S3_ENDPOINT`, and the provision pod has a host.
 * @param {string} [endpoint='"$S3_ENDPOINT"']
 * @returns {string}
 */
export function rcloneStore(endpoint = '"$S3_ENDPOINT"') {
    return [
        "export RCLONE_CONFIG=/dev/null RCLONE_CONFIG_STORE_TYPE=s3 RCLONE_CONFIG_STORE_PROVIDER=Other",
        `RCLONE_CONFIG_STORE_ENDPOINT=${endpoint} RCLONE_CONFIG_STORE_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID"`,
        'RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" RCLONE_CONFIG_STORE_FORCE_PATH_STYLE=true',
        "RCLONE_CONFIG_STORE_REGION=auto RCLONE_CONFIG_STORE_NO_CHECK_BUCKET=true",
    ].join(" ");
}

/**
 * Runs one shell command from a short-lived Job and fails when it fails.
 * @param {Kubernetes} k8s
 * @param {string} label
 * @param {string} command
 * @param {CommandJobOptions} [options={}]
 */
export function runCommandJob(k8s, label, command, options = {}) {
    const name = label.replace(/_/g, "-").slice(0, 40);

    const spec = podJobSpec({
        container: name,
        command: ["/bin/sh"],
        args: ["-c", command],
        image: options.image ?? PG_IMAGE,
        env: options.env,
        serviceAccountName: options.serviceAccountName,
        podSpec: options.podSpec ?? workerPodSpec(k8s),
    });

    waitForJob(k8s, createJob(k8s, `e2e-${name}-${Date.now()}`, spec));
}

/**
 * Runs the configure-db script again from the suspended CronJob of the chart, as an operator would. `rules` is a shell
 * command that prints the rules file, so that a Job can run the script with other rules than the chart gives.
 * @param {Kubernetes} k8s
 * @param {string} label
 * @param {string} [rules="cat /scripts/rbac.toml"]
 */
export function rerunConfigureDb(k8s, label, rules = "cat /scripts/rbac.toml") {
    const name = label.replace(/_/g, "-").slice(0, 40);

    const spec = podJobSpec({
        container: "configure-db",
        command: ["/bin/sh"],
        args: [
            "-c",
            `set -e; ${rules} > /tmp/rbac.toml; cd /scripts; RBAC_FILE=/tmp/rbac.toml nu /scripts/configure-db.nu`,
        ],
        podSpec: cronJobPodSpec(k8s, CONFIGURE_DB_CRONJOB),
    });

    waitForJob(k8s, createJob(k8s, `e2e-${name}-${Date.now()}`, spec));
}

/**
 * Shell test that succeeds when one SQL query prints the expected value. The DSN is a shell word.
 * @param {string} query
 * @param {string} expected
 * @param {string} dsn
 * @returns {string}
 */
export function sqlEquals(query, expected, dsn) {
    return `test "$(psql ${dsn} -tAc ${JSON.stringify(query)})" = ${JSON.stringify(expected)}`;
}

/**
 * Fails the run unless one SQL query prints the expected value.
 * @param {Kubernetes} k8s
 * @param {string} label
 * @param {string} query
 * @param {string} expected
 * @param {Database} [databaseVariable="PG_DATABASE_URL"]
 */
export function checkSql(k8s, label, query, expected, databaseVariable = "PG_DATABASE_URL") {
    runCommandJob(k8s, label, sqlEquals(query, expected, `"$${databaseVariable}"`));
}

/**
 * Runs one SQL statement in a database from a short-lived Job.
 * @param {Kubernetes} k8s
 * @param {string} label
 * @param {string} statement
 * @param {Database} [databaseVariable="PG_DATABASE_URL"]
 */
export function runSqlJob(k8s, label, statement, databaseVariable = "PG_DATABASE_URL") {
    runCommandJob(k8s, label, `psql "$${databaseVariable}" -v ON_ERROR_STOP=1 -c ${JSON.stringify(statement)}`);
}

/**
 * Clears the proxy cache with a short-lived Valkey Job.
 * @param {Kubernetes} k8s
 */
export function clearProxyCache(k8s) {
    runCommandJob(
        k8s,
        "clear-proxy-cache",
        'valkey-cli -h "$VALKEY_HOST" -p "$VALKEY_PORT" -a "$VALKEY_PASSWORD" --no-auth-warning -n "$PROXY_CACHE_REDIS_DB" FLUSHDB',
        {
            image: "docker.io/valkey/valkey:8-alpine",
            env: [
                { name: "VALKEY_HOST", value: "data-proxy-valkey" },
                { name: "VALKEY_PORT", value: "6379" },
                {
                    name: "VALKEY_PASSWORD",
                    valueFrom: {
                        secretKeyRef: { name: "data-proxy-redis", key: "REDIS_PASSWORD" },
                    },
                },
            ],
        },
    );
}

/**
 * Force-deletes the sync pod that executes the running sync workflow (DBOS reports it as PENDING).
 * @param {Kubernetes} k8s
 */
export function killWorkflowOwner(k8s) {
    const image = cronJobPodSpec(k8s, MAINTENANCE_CRONJOB).containers[0].image;

    const owner =
        "SELECT executor_id FROM dbos.workflow_status WHERE name = 'run_sync' AND status = 'PENDING' AND executor_id IS NOT NULL ORDER BY created_at DESC LIMIT 1";

    const script = [
        "set -e",
        "for attempt in $(seq 1 60); do",
        `  uid="$(psql "$${STATE_DATABASE}" -tAc ${JSON.stringify(owner)})"`,
        '  test -n "$uid" && break',
        "  sleep 1",
        "done",
        'test -n "$uid"',
        `pod="$(kubectl get pods -n ${NAMESPACE} -l app.kubernetes.io/component=sync -o jsonpath="{.items[?(@.metadata.uid==\\"$uid\\")].metadata.name}")"`,
        'test -n "$pod"',
        `kubectl delete pod "$pod" -n ${NAMESPACE} --grace-period=0 --force`,
    ].join("\n");

    runCommandJob(k8s, "kill-workflow-owner", script, {
        image,
        serviceAccountName: "manifests-k6-e2e",
    });
}

/**
 * The pod spec options of a Job that runs `kubectl` in the namespace with the service account of the e2e runner.
 * @param {Kubernetes} k8s
 * @returns {CommandJobOptions}
 */
function kubectlOptions(k8s) {
    return {
        image: cronJobPodSpec(k8s, MAINTENANCE_CRONJOB).containers[0].image,
        serviceAccountName: "manifests-k6-e2e",
    };
}

/**
 * Force-deletes one pod with a Job, so that its controller has to start a new one.
 * @param {Kubernetes} k8s
 * @param {string} name
 */
export function killPod(k8s, name) {
    runCommandJob(
        k8s,
        `kill-${name}`,
        `kubectl delete pod "${name}" -n ${NAMESPACE} --grace-period=0 --force`,
        kubectlOptions(k8s),
    );
}

/**
 * Reports whether the recent log of the pods with a label contains a text, read by a Job.
 * @param {Kubernetes} k8s
 * @param {string} label
 * @param {string} selector
 * @param {string} text
 * @returns {boolean}
 */
export function logHas(k8s, label, selector, text) {
    try {
        runCommandJob(
            k8s,
            label,
            `test "$(kubectl logs -n ${NAMESPACE} -l ${selector} --all-containers --since=10m --tail=500 | grep -c ${JSON.stringify(text)})" -gt 0`,
            kubectlOptions(k8s),
        );

        return true;
    } catch {
        return false;
    }
}
