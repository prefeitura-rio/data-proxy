/** @import { Kubernetes } from 'k6/x/kubernetes' */
import {
    API_URL,
    authHeaders,
    BIG_TABLE,
    checkSql,
    clusterObject,
    clusterScalerAutoscales,
    clusterSettled,
    MIN_INSTANCES,
    NAMESPACE,
    pgdogAvailable,
    postgresPodsMountSecret,
    POSTGREST_URL,
    rowsOf,
    runCommandJob,
    scenario,
    SCHEMA,
    seedAccessPolicy,
    sourceHeader,
    sqlEquals,
    TABLES,
    uncachedReads,
    userToken,
    waitForAccessPolicyReplication,
    waitUntil,
} from "../lib/index.js";

const { expect } = scenario("topology");
const TOPOLOGY_TIMEOUT_SECONDS = Number(__ENV.TOPOLOGY_TIMEOUT_SECONDS || "900");
const SERVING = "serving";

/**
 * Verifies that PostgREST reloaded its schema after the views changed, without a restart, and is available.
 * @param {Kubernetes} k8s
 */
export function servingReload(k8s) {
    const deployments = k8s.list("Deployment.apps", NAMESPACE);

    const postgrests = deployments.filter(
        (item) => item.metadata.labels?.["app.kubernetes.io/component"] === "postgrest",
    );

    expect("the PostgREST Deployment is discoverable by label", postgrests.length === 1);

    expect(
        "PostgREST reloaded without a restart",
        postgrests.every(
            (item) => item.spec.template.metadata?.annotations?.["kubectl.kubernetes.io/restartedAt"] === undefined,
        ),
    );

    expect(
        "the PostgREST Deployment has all replicas available",
        waitUntil(
            TOPOLOGY_TIMEOUT_SECONDS,
            () =>
                k8s
                    .list("Deployment.apps", NAMESPACE)
                    .filter((item) => postgrests.some((known) => known.metadata.name === item.metadata.name))
                    .every((item) => (item.status?.availableReplicas ?? 0) === (item.spec.replicas ?? 0)),
            "the PostgREST replicas to be available",
        ),
    );
}

/**
 * Shell word of the connection string of a standby: the read-only service of the same cluster.
 * @type {"\"$(echo \"$PG_DATABASE_URL\" | sed 's/-rw:/-ro:/')\""}
 */
const STANDBY_DSN = `"$(echo "$PG_DATABASE_URL" | sed 's/-rw:/-ro:/')"`;

/**
 * Verifies the standbys, and that PgDog sends PostgREST reads to them and its listener to the primary.
 * @param {Kubernetes} k8s
 */
function verifyStandbys(k8s) {
    checkSql(
        k8s,
        "streaming-standbys",
        `SELECT count(*) >= ${MIN_INSTANCES - 1} FROM pg_stat_replication WHERE state = 'streaming'`,
        "t",
    );

    checkSql(
        k8s,
        "postgrest-listener-on-primary",
        `SELECT count(*) > 0 FROM pg_stat_activity WHERE usename = '${SERVING}' AND query LIKE 'LISTEN%'`,
        "t",
    );
}

/**
 * Verifies that parallel reads succeed and that PgDog opened backends on a standby for them.
 * @param {Kubernetes} k8s
 */
function verifyReadsReachStandby(k8s) {
    const reads = uncachedReads(POSTGREST_URL, {
        Authorization: `Bearer ${userToken()}`,
        "Accept-Profile": SCHEMA,
    });

    expect(
        "every parallel read on PostgREST succeeds",
        reads.every((response) => response.status === 200),
    );

    expect(
        "every parallel read on PostgREST is served from DuckLake",
        reads.every((response) => sourceHeader(response).indexOf("ducklake") !== -1),
    );

    runCommandJob(
        k8s,
        "reads-reach-the-standby",
        sqlEquals(
            `SELECT count(*) > 0 FROM pg_stat_activity WHERE usename = '${SERVING}' AND backend_type = 'client backend'`,
            "t",
            STANDBY_DSN,
        ),
    );
}

/**
 * Verifies the deployed topology and the read and write paths.
 * @param {Kubernetes} k8s
 */
export function topology(k8s) {
    const timeout = TOPOLOGY_TIMEOUT_SECONDS;

    expect(
        "the cluster settles on a primary and a standby",
        waitUntil(timeout, () => clusterSettled(k8s), "the cluster to settle on a primary and a standby"),
    );

    expect(
        "PgDog is available",
        waitUntil(timeout, () => pgdogAvailable(k8s), "PgDog to be available"),
    );

    expect(
        "the cluster scaler autoscales the instances",
        waitUntil(timeout, () => clusterScalerAutoscales(k8s), "the cluster scaler to own the instance count"),
    );

    expect(
        "every PostgreSQL pod mounts the shared S3 secret",
        postgresPodsMountSecret(k8s, clusterObject(k8s).spec.instances),
    );

    checkSql(
        k8s,
        "topology-state-intact",
        "SELECT count(*) FROM data_proxy.state",
        String([...TABLES, BIG_TABLE].length),
        "DBOS_SYSTEM_DATABASE_URL",
    );

    verifyStandbys(k8s);
    seedAccessPolicy();
    waitForAccessPolicyReplication(userToken());
    const read = uncachedReads(API_URL, authHeaders(userToken()));

    expect(
        "every read through the proxy succeeds",
        read.every((response) => response.status === 200),
    );

    expect(
        "every read through the proxy returns rows",
        read.every((response) => rowsOf(response).length > 0),
    );

    expect(
        "every read through the proxy is served from DuckLake",
        read.every((response) => sourceHeader(response).indexOf("ducklake") !== -1),
    );

    verifyReadsReachStandby(k8s);
}

/** @type {Step[]} */
export const steps = [
    { scenario: "topology", name: "servingReload", stage: 150, run: ({ k8s }) => servingReload(k8s) },
    { scenario: "topology", name: "topology", stage: 330, needsData: false, run: ({ k8s }) => topology(k8s) },
];
