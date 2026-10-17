/** @import { Response as K6Response } from 'k6/http' */
/** @import { Kubernetes, KubernetesCluster, KubernetesKinds, KubernetesPod } from 'k6/x/kubernetes' */
import http from "k6/http";
import {
    CLUSTER_NAME,
    CLUSTER_SCALER,
    FULL_TABLE,
    MIN_INSTANCES,
    NAMESPACE,
    PARALLEL_READS,
    PAUSED_REPLICAS,
    PGDOG,
    SECRETS_MOUNT,
    SECRETS_SUBPATH,
} from "./env.js";

/**
 * Returns the ready PostgreSQL instance Pods of the cluster.
 * @param {Kubernetes} k8s
 * @returns {KubernetesPod[]}
 */
export function readyInstances(k8s) {
    const pods = k8s.list("Pod", NAMESPACE);

    return pods.filter(
        (pod) =>
            pod.metadata.labels?.["cnpg.io/podRole"] === "instance" &&
            Boolean(pod.status?.podIP) &&
            pod.status?.conditions?.some((condition) => condition.type === "Ready" && condition.status === "True"),
    );
}

/**
 * Lists pods that match a component label.
 * @param {Kubernetes} k8s
 * @param {string} component
 * @returns {KubernetesPod[]}
 */
export function podsForComponent(k8s, component) {
    const pods = k8s.list("Pod", NAMESPACE);

    return pods.filter((pod) => (pod.metadata.labels?.["app.kubernetes.io/component"] || "") === component);
}

/**
 * Reads the PostgREST deployment revision from the Kubernetes API.
 * @param {Kubernetes} k8s
 * @returns {string}
 */
export function postgrestDeploymentRevision(k8s) {
    const deployment = k8s.get("Deployment.apps", "data-proxy-postgrest", NAMESPACE);

    return String(deployment.status?.observedGeneration || "0");
}

/**
 * @template {"Deployment.apps" | "ScaledObject.keda.sh"} K
 * @param {Kubernetes} k8s
 * @param {K} kind
 * @param {string} name
 * @returns {KubernetesKinds[K] | undefined}
 */
export function findObject(k8s, kind, name) {
    return k8s.list(kind, NAMESPACE).find((item) => item.metadata.name === name);
}

/**
 * @param {Kubernetes} k8s
 * @returns {KubernetesCluster}
 */
export function clusterObject(k8s) {
    return k8s.get("Cluster.postgresql.cnpg.io", CLUSTER_NAME, NAMESPACE);
}

/**
 * Reports whether the Cluster has a primary and a standby, and all instances are ready.
 * @param {Kubernetes} k8s
 * @returns {boolean}
 */
export function clusterSettled(k8s) {
    const cluster = clusterObject(k8s);
    const instances = cluster.spec.instances;

    return (
        instances >= MIN_INSTANCES &&
        cluster.status?.readyInstances === instances &&
        cluster.status?.phase === "Cluster in healthy state"
    );
}

/**
 * Reports whether PgDog is available.
 * @param {Kubernetes} k8s
 * @returns {boolean}
 */
export function pgdogAvailable(k8s) {
    return (findObject(k8s, "Deployment.apps", PGDOG)?.status?.availableReplicas ?? 0) >= 1;
}

/**
 * Reports whether the cluster scaler owns the instance count.
 * @param {Kubernetes} k8s
 * @returns {boolean}
 */
export function clusterScalerAutoscales(k8s) {
    const scaler = findObject(k8s, "ScaledObject.keda.sh", CLUSTER_SCALER);

    return scaler !== undefined && scaler.metadata.annotations?.[PAUSED_REPLICAS] === undefined;
}

/**
 * Reports whether every PostgreSQL pod mounts the shared S3 secret folder.
 * @param {Kubernetes} k8s
 * @param {number} expected
 * @returns {boolean}
 */
export function postgresPodsMountSecret(k8s, expected) {
    const pods = k8s
        .list("Pod", NAMESPACE)
        .filter(
            (pod) =>
                pod.metadata.labels?.["cnpg.io/cluster"] === CLUSTER_NAME &&
                pod.metadata.labels?.["cnpg.io/instanceName"] !== undefined,
        );

    return (
        pods.length === expected &&
        pods.every((pod) =>
            pod.spec.containers.some((container) =>
                (container.volumeMounts ?? []).some(
                    (mount) => mount.mountPath === SECRETS_MOUNT && mount.subPath === SECRETS_SUBPATH,
                ),
            ),
        )
    );
}

/**
 * Sends parallel uncached reads, so the pool of the read PostgREST opens several connections.
 * @param {string} baseUrl
 * @param {Record<string, string>} headers
 * @returns {K6Response[]}
 */
export function uncachedReads(baseUrl, headers) {
    const seed = Date.now();

    return http.batch(
        Array.from({ length: PARALLEL_READS }, (_, index) => ({
            method: "GET",
            url: `${baseUrl}/${FULL_TABLE}?select=id&limit=${100 + ((seed + index) % 800)}`,
            params: { headers, tags: { name: "mode_uncached_read" } },
        })),
    );
}

/**
 * @param {Kubernetes} k8s
 * @returns {Record<string, number>}
 */
export function scalingReplicaCounts(k8s) {
    const deployments = k8s.list("Deployment.apps", NAMESPACE);

    /** @type {Record<string, number>} */
    const baseline = {};

    for (const deployment of deployments) {
        const component = deployment.metadata.labels?.["app.kubernetes.io/component"];

        if (component === "proxy" || component === "postgrest" || component === "pgdog") {
            baseline[deployment.metadata.name] = deployment.spec.replicas || 0;
        }
    }

    return baseline;
}

/**
 * Reads the Prometheus metrics of one pod of a component, from its admin port.
 * @param {Kubernetes} k8s
 * @param {string} component
 * @param {number} [port=3001]
 * @returns {K6Response | undefined}
 */
export function podMetrics(k8s, component, port = 3001) {
    const ip = podsForComponent(k8s, component).find((pod) => pod.status?.podIP !== undefined)?.status?.podIP;

    return ip === undefined
        ? undefined
        : http.get(`http://${ip}:${port}/metrics`, { tags: { name: `${component}:metrics` } });
}
