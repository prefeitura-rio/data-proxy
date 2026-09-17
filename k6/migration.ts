import http from "k6/http";
import { check, sleep } from "k6";
import { Kubernetes } from "k6/x/kubernetes";

import { NAMESPACE } from "./lib.ts";

type K6Response = {
    status: number;
    json: () => unknown;
};

type KubernetesResource = {
    metadata: { name: string };
    status?: {
        conditions?: Array<{ type: string; status: string }>;
        succeeded?: number;
    };
};

type Fingerprint = {
    tables: Record<string, number>;
    accessPolicy: number;
};

declare const __ENV: Record<string, string | undefined>;

const API_URL =
    __ENV.BASE_URL ||
    "http://istio-ingressgateway.istio-ingress.svc.cluster.local";
const HOST = __ENV.API_HOST || "data-proxy.local";
const OIDC_TOKEN_URL =
    __ENV.OIDC_TOKEN_URL || "http://oidc.data-proxy.svc.cluster.local:8080/token";
const OIDC_CLIENT_SECRET = __ENV.OIDC_CLIENT_SECRET || "test-secret";
const PHASE = __ENV.MIGRATION_PHASE || "shared-baseline";
const TIMEOUT_SECONDS = Number(__ENV.MIGRATION_TIMEOUT_SECONDS || "360");
const POLL_SECONDS = Number(__ENV.MIGRATION_POLL_INTERVAL_SECONDS || "2");
const FINGERPRINT_CONFIGMAP = "data-proxy-migration-fingerprint";
const SCHEMA = "pic";
const TABLES = [
    "endpoint_participante_listagem",
    "endpoint_participantes",
    "protocolo_estado_diario",
];

export const options = {
    scenarios: {
        migration: {
            executor: "shared-iterations",
            vus: 1,
            iterations: 1,
            maxDuration: `${TIMEOUT_SECONDS}s`,
        },
    },
    thresholds: { checks: ["rate==1"] },
};

/** Gets a short-lived test token without logging its value. */
function fetchToken(clientId: string): string {
    const response = http.post(OIDC_TOKEN_URL, {
        grant_type: "client_credentials",
        client_id: clientId,
        client_secret: OIDC_CLIENT_SECRET,
    }) as K6Response;

    if (response.status !== 200) {
        throw new Error(`token request for ${clientId} returned ${response.status}`);
    }
    return (response.json() as { access_token: string }).access_token;
}

/** Sends an authenticated read through the public proxy for the pic schema. */
function proxyGet(path: string, token: string): K6Response {
    return http.get(`${API_URL}${path}`, {
        headers: {
            Host: HOST,
            Authorization: `Bearer ${token}`,
            "Accept-Profile": SCHEMA,
        },
    }) as K6Response;
}

/** Returns an array response, or fails without exposing response contents. */
function rows(response: K6Response): unknown[] {
    const value = response.json();
    if (!Array.isArray(value)) throw new Error(`expected an array response, got ${response.status}`);
    return value;
}

/** Sends an authenticated read directly to the active PostgREST service. */
function postgrestGet(path: string, token: string, ha: boolean): K6Response {
    const service = ha ? "data-proxy-pic-postgrest-ro" : "data-proxy-postgrest-ro";
    return http.get(
        `http://${service}.${NAMESPACE}.svc.cluster.local:3000${path}`,
        {
            headers: {
                Authorization: `Bearer ${token}`,
                "Accept-Profile": SCHEMA,
            },
        },
    ) as K6Response;
}

/** Captures local PostgreSQL row and access-policy counts without nginx fallback. */
function localFingerprint(token: string, ha: boolean): Fingerprint {
    const tables: Record<string, number> = {};

    for (const table of TABLES) {
        const response = postgrestGet(`/${table}?limit=1000`, token, ha);
        if (response.status !== 200) throw new Error(`${table} returned ${response.status}`);
        tables[table] = rows(response).length;
    }

    const policy = postgrestGet("/access_policy?subject=eq.user-1&limit=1000", token, ha);
    if (policy.status !== 200) throw new Error(`access_policy returned ${policy.status}`);
    return { tables, accessPolicy: rows(policy).length };
}

/** Waits for a named Kubernetes resource to satisfy a phase-specific predicate. */
function waitForResource(
    k8s: Kubernetes,
    kind: string,
    name: string,
    predicate: (resource: KubernetesResource) => boolean,
): void {
    const deadline = Date.now() + TIMEOUT_SECONDS * 1000;

    while (Date.now() < deadline) {
        try {
            const resource = k8s.get(kind, name, NAMESPACE) as KubernetesResource;
            if (predicate(resource)) return;
        } catch (_) {
            // The target resource may not exist while Helm creates it.
        }
        sleep(POLL_SECONDS);
    }
    throw new Error(`${PHASE}: ${kind}/${name} did not reach its expected state`);
}

/** Returns true only when a resource is absent; Kubernetes errors are expected here. */
function resourceIsAbsent(k8s: Kubernetes, kind: string, name: string): boolean {
    try {
        k8s.get(kind, name, NAMESPACE);
        return false;
    } catch (_) {
        return true;
    }
}

/** Waits for every data-plane resource required by the selected architecture. */
function waitForTopology(k8s: Kubernetes, ha: boolean): void {
    const prefix = ha ? "data-proxy-pic" : "data-proxy";

    waitForResource(k8s, "Cluster.postgresql.cnpg.io", prefix, (resource) =>
        resource.status?.conditions?.some((condition) => condition.type === "Ready" && condition.status === "True") || false,
    );

    waitForResource(k8s, "Pooler.postgresql.cnpg.io", `${prefix}-pooler-ro`, () => true);

    for (const component of ["nginx-proxy", "postgrest-ro", "postgrest-rw"]) {
        waitForResource(k8s, "Deployment.apps", `${prefix}-${component}`, (resource) =>
            resource.status?.conditions?.some((condition) => condition.type === "Available" && condition.status === "True") || false,
        );
    }
}

/** Waits for a Helm-created migration Job to complete after a topology transition. */
function waitForMigrationJob(k8s: Kubernetes, direction: string): void {
    const deadline = Date.now() + TIMEOUT_SECONDS * 1000;

    while (Date.now() < deadline) {
        const jobs = k8s.list("Job.batch", NAMESPACE) as KubernetesResource[];
        if (jobs.some((job) => job.metadata.name.startsWith("data-proxy-migrate-") && job.status?.succeeded)) {
            verifyModeState(k8s, direction);
            return;
        }
        sleep(POLL_SECONDS);
    }
    throw new Error(`${PHASE}: migration Job for ${direction} did not complete`);
}

/** Verifies the mode-state ConfigMap records the expected direction and completed status. */
function verifyModeState(k8s: Kubernetes, expectedDirection: string): void {
    const config = k8s.get("ConfigMap", "data-proxy-mode-state", NAMESPACE) as {
        data?: { direction?: string; status?: string };
    };

    if (config.data?.direction !== expectedDirection || config.data?.status !== "completed") {
        throw new Error(`${PHASE}: mode-state expected direction=${expectedDirection} status=completed, got direction=${config.data?.direction} status=${config.data?.status}`);
    }
}

/** Stores the shared baseline fingerprint for independent later TestRun Pods. */
function saveBaseline(k8s: Kubernetes, value: Fingerprint): void {
    k8s.create({
        apiVersion: "v1",
        kind: "ConfigMap",
        metadata: { name: FINGERPRINT_CONFIGMAP, namespace: NAMESPACE },
        data: { fingerprint: JSON.stringify(value) },
    });
}

/** Loads the safe baseline fingerprint and fails if the baseline phase did not run. */
function loadBaseline(k8s: Kubernetes): Fingerprint {
    const config = k8s.get("ConfigMap", FINGERPRINT_CONFIGMAP, NAMESPACE) as {
        data?: { fingerprint?: string };
    };

    if (!config.data?.fingerprint) throw new Error("migration baseline fingerprint is absent");

    return JSON.parse(config.data.fingerprint) as Fingerprint;
}

/** Waits for the public pic API to accept requests after topology changes. */
function waitForApi(k8s: Kubernetes, token: string): void {
    const deadline = Date.now() + TIMEOUT_SECONDS * 1000;
    while (Date.now() < deadline) {
        const response = proxyGet("/endpoint_participante_listagem?limit=1", token);
        if (response.status === 200) return;
        sleep(POLL_SECONDS);
    }
    throw new Error(`${PHASE}: public pic API did not become ready within ${TIMEOUT_SECONDS}s`);
}

/** Runs the assertions selected by MIGRATION_PHASE. */
export default function migration(): void {
    const k8s = new Kubernetes();
    const ha = PHASE === "ha" || PHASE === "ha-settled";

    if (PHASE === "shared-baseline") {
        waitForTopology(k8s, false);
    } else {
        if (PHASE === "ha") waitForMigrationJob(k8s, "to-ha");
        if (PHASE === "shared-return") waitForMigrationJob(k8s, "to-single");
        waitForTopology(k8s, ha);
    }

    const authorized = fetchToken("user-with-access");
    const noAccess = fetchToken("user-no-access");
    waitForApi(k8s, authorized);
    const current = localFingerprint(authorized, ha);
    const authenticated = proxyGet("/endpoint_participante_listagem?limit=10", authorized);
    const denied = proxyGet("/endpoint_participante_listagem?limit=10", noAccess);

    check(null, {
        [`${PHASE}: authenticated API response`]: () => authenticated.status === 200,
        [`${PHASE}: no-policy RLS`]: () => denied.status === 200 && rows(denied).length === 0,
    });

    if (PHASE === "shared-baseline") {
        saveBaseline(k8s, current);
        return;
    }

    check(null, {
        [`${PHASE}: fingerprint equals baseline`]: () =>
            JSON.stringify(current) === JSON.stringify(loadBaseline(k8s)),
    });

    if (PHASE === "ha-settled") {
        const sharedResources = [
            ["Cluster.postgresql.cnpg.io", "data-proxy"],
            ["Pooler.postgresql.cnpg.io", "data-proxy-pooler-ro"],
            ["Deployment.apps", "data-proxy-nginx-proxy"],
            ["Deployment.apps", "data-proxy-postgrest-ro"],
            ["Deployment.apps", "data-proxy-postgrest-rw"],
            ["ScaledObject.keda.sh", "data-proxy-nginx-proxy"],
            ["ScaledObject.keda.sh", "data-proxy-postgrest-ro"],
            ["ScaledObject.keda.sh", "data-proxy-pooler-ro-autoscaler"],
            ["HorizontalPodAutoscaler.autoscaling", "data-proxy-postgrest-rw"],
            ["Service", "data-proxy-nginx-proxy"],
            ["Service", "data-proxy-postgrest-ro"],
            ["Service", "data-proxy-postgrest-rw"],
            ["PodDisruptionBudget.policy", "data-proxy-postgrest-ro"],
            ["PodDisruptionBudget.policy", "data-proxy-postgrest-rw"],
            ["CronJob.batch", "data-proxy-cleanup"],
            ["CronJob.batch", "data-proxy-partman"],
        ];
        check(null, {
            "ha-settled: shared source pruned": () =>
                sharedResources.every(([kind, name]) => resourceIsAbsent(k8s, kind, name)),
        });
    }

    if (PHASE === "shared-settled") {
        const haResources = [
            ["Cluster.postgresql.cnpg.io", "data-proxy-pic"],
            ["Pooler.postgresql.cnpg.io", "data-proxy-pic-pooler-ro"],
            ["Deployment.apps", "data-proxy-pic-nginx-proxy"],
            ["Deployment.apps", "data-proxy-pic-postgrest-ro"],
            ["Deployment.apps", "data-proxy-pic-postgrest-rw"],
            ["ScaledObject.keda.sh", "data-proxy-pic-nginx-proxy"],
            ["ScaledObject.keda.sh", "data-proxy-pic-postgrest-ro"],
            ["ScaledObject.keda.sh", "data-proxy-pic-pooler-ro-autoscaler"],
            ["HorizontalPodAutoscaler.autoscaling", "data-proxy-pic-postgrest-rw"],
            ["Service", "data-proxy-pic-nginx-proxy"],
            ["Service", "data-proxy-pic-postgrest-ro"],
            ["Service", "data-proxy-pic-postgrest-rw"],
            ["PodDisruptionBudget.policy", "data-proxy-pic-pooler-ro"],
            ["PodDisruptionBudget.policy", "data-proxy-pic-postgrest-ro"],
            ["PodDisruptionBudget.policy", "data-proxy-pic-postgrest-rw"],
            ["CronJob.batch", "data-proxy-pic-cleanup"],
            ["CronJob.batch", "data-proxy-pic-partman"],
        ];
        check(null, {
            "shared-settled: HA source pruned": () =>
                haResources.every(([kind, name]) => resourceIsAbsent(k8s, kind, name)),
        });
    }
}
