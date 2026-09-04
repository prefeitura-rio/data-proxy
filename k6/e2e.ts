import http from "k6/http";
import { Kubernetes } from "k6/x/kubernetes";
import { check, sleep } from "k6";

type K6Response = {
    status: number;
    body: string;
    json: (path?: string) => unknown;
};
type FreshnessRow = {
    table: string;
    strategy: string;
    partition: string | null;
    status: string;
};

interface MetricRequest {
    stage: string;
    source: string;
    metric: string;
    method: string;
    url: string;
    params: Record<string, unknown>;
    extract: (r: K6Response) => unknown;
}

declare const __ENV: Record<string, string | undefined>;

const API_URL = __ENV.BASE_URL || "http://istio-ingressgateway.istio-ingress.svc.cluster.local";
const OIDC_TOKEN_URL = __ENV.OIDC_TOKEN_URL || "http://oidc.data-proxy.svc.cluster.local:8080/token";
const OIDC_CLIENT_ID = __ENV.OIDC_CLIENT_ID || "user-with-access";
const OIDC_CLIENT_SECRET = __ENV.OIDC_CLIENT_SECRET || "test-secret";
const HOST = __ENV.API_HOST || "data-proxy.local";
const WEBDIS_URL = __ENV.WEBDIS_URL || "http://webdis.data-proxy.svc.cluster.local:7379";
const SCHEMA = "pic";
const NAMESPACE = __ENV.NAMESPACE || "data-proxy";
const PRODUCER_CRONJOB = __ENV.PRODUCER_CRONJOB || "data-proxy-producer";
const POLL_INTERVAL = 2;
const MAX_DURATION = __ENV.MAX_DURATION || "10m";

const FULL_TABLE = "endpoint_participante_listagem";
const MULTI_RLS_TABLE = "endpoint_participantes";
const PARTITIONED_TABLE = "protocolo_estado_diario";
const STREAMS = ["dp:extract", "dp:prepare", "dp:publish"];
const TABLES = [FULL_TABLE, MULTI_RLS_TABLE, PARTITIONED_TABLE];

const ACCESS_POLICY_ROWS = [
    { subject: "user-1", unit_type: "unidade", unit_id: "cras_1" },
    { subject: "user-1", unit_type: "cras", unit_id: "cras_1" },
    { subject: "user-1", unit_type: "escola", unit_id: "escola_1" },
];

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
};

function now(): string {
    return new Date().toISOString();
}

function log(stage: string, source: string, metric: string, value: unknown): void {
    console.log(JSON.stringify({ time: now(), stage, source, metric, value }));
}

function safeJson(r: K6Response): unknown {
    if (r.status === 0 || !r.body) return null;
    try {
        return r.json();
    } catch {
        return null;
    }
}

function fetchToken(clientId: string = OIDC_CLIENT_ID): string {
    const response = http.post(OIDC_TOKEN_URL, {
        grant_type: "client_credentials",
        client_id: clientId,
        client_secret: OIDC_CLIENT_SECRET,
    }) as K6Response;
    const body = safeJson(response) as { access_token?: string } | null;
    if (!body?.access_token) {
        throw new Error(`Token request failed: ${response.body}`);
    }
    return body.access_token;
}

function authHeaders(token: string): Record<string, string> {
    return {
        Authorization: `Bearer ${token}`,
        Host: HOST,
        "Accept-Profile": SCHEMA,
    };
}

function triggerSync(k8s: Kubernetes): string {
    const cronJob = k8s.get("CronJob.batch", PRODUCER_CRONJOB, NAMESPACE) as {
        spec: { jobTemplate: { spec: object } };
    };

    const jobName = `data-proxy-producer-e2e-${Date.now()}`;
    const job = {
        apiVersion: "batch/v1",
        kind: "Job",
        metadata: { name: jobName, namespace: NAMESPACE },
        spec: cronJob.spec.jobTemplate.spec,
    };

    k8s.create(job);
    log("sync", "k8s", "job_created", jobName);
    return jobName;
}

function waitForSyncJob(k8s: Kubernetes, jobName: string): void {
    const deadline = Date.now() + 300_000;
    while (Date.now() < deadline) {
        const job = k8s.get("Job.batch", jobName, NAMESPACE) as {
            status?: { succeeded?: number; failed?: number };
        };
        if (job.status?.succeeded && job.status.succeeded > 0) {
            log("sync", "k8s", "job_completed", jobName);
            return;
        }
        if (job.status?.failed && job.status.failed > 0) {
            throw new Error(`Sync job ${jobName} failed`);
        }
        sleep(1);
    }
    throw new Error(`Sync job ${jobName} timed out`);
}

function seedAccessPolicy(): void {
    const token = fetchToken("policy-writer");
    const headers = {
        Authorization: `Bearer ${token}`,
        Host: HOST,
        "Accept-Profile": SCHEMA,
        "Content-Type": "application/json",
        Prefer: "resolution=merge-duplicates",
    };
    let status = 0;
    for (let attempt = 0; attempt < 3; attempt++) {
        const response = http.post(
            `${API_URL}/access_policy`,
            JSON.stringify(ACCESS_POLICY_ROWS),
            { headers, tags: { name: "seed_access_policy" } },
        ) as K6Response;
        status = response.status;
        if (status === 201 || status === 409) break;
        sleep(1);
    }
    log("setup", "postgrest", "seed_access_policy_status", status);
    check(null, {
        "access_policy seeded": () => status === 201 || status === 409,
    });
}

function verifyNoAccess(): void {
    const token = fetchToken("user-no-access");
    let status = 0;
    let body: unknown = null;
    for (let attempt = 0; attempt < 3; attempt++) {
        const response = http.get(
            `${API_URL}/endpoint_participante_listagem?limit=1`,
            { headers: authHeaders(token), tags: { name: "no_access_check" } },
        ) as K6Response;
        status = response.status;
        body = safeJson(response);
        if (status === 200) break;
        sleep(1);
    }
    check(null, {
        "user without policy gets 200": () => status === 200,
        "no-access returns zero rows": () => Array.isArray(body) && body.length === 0,
    });
}

function redisMetric(metric: string, command: string, extract: (r: K6Response) => unknown): MetricRequest {
    return {
        stage: "sync",
        source: "redis",
        metric,
        method: "GET",
        url: `${WEBDIS_URL}/${command}`,
        params: { tags: { name: `redis:${metric}` } },
        extract,
    };
}

function postgrestMetric(stage: string, source: string, metric: string, path: string, token: string, extract: (r: K6Response) => unknown): MetricRequest {
    return {
        stage,
        source,
        metric,
        method: "GET",
        url: `${API_URL}${path}`,
        params: { headers: authHeaders(token), tags: { name: metric } },
        extract,
    };
}

function buildMetrics(token: string): MetricRequest[] {
    const metrics: MetricRequest[] = [];

    const groups: Record<string, string> = {
        "dp:extract": "dumpers",
        "dp:prepare": "seeders",
        "dp:publish": "publishers",
    };

    for (const stream of STREAMS) {
        metrics.push(redisMetric(
            `stream_length:${stream}`,
            `XPENDING/${stream}/${groups[stream]}`,
            (r) => {
                if (r.status !== 200) return -1;
                const body = safeJson(r) as { XPENDING?: { msgs?: number } } | null;
                return Number(body?.XPENDING?.msgs ?? -1);
            },
        ));
    }

    metrics.push(redisMetric("db_size", "DBSIZE", (r) => {
        if (r.status !== 200) return -1;
        const body = safeJson(r) as { DBSIZE?: string } | null;
        return Number(body?.DBSIZE ?? -1);
    }));

    metrics.push(redisMetric("active_run", "GET/dp:active", (r) => {
        if (r.status !== 200) return null;
        const body = safeJson(r) as { GET?: string | null } | null;
        return body?.GET ?? null;
    }));

    for (const table of TABLES) {
        metrics.push(postgrestMetric("extract", "postgrest", `table_status:${table}`, `/${table}?limit=1`, token, (r) => r.status));
        metrics.push(postgrestMetric("publish", "postgrest", `table_row_count:${table}`, `/${table}?limit=1000`, token, (r) => {
            const body = safeJson(r);
            return Array.isArray(body) ? body.length : 0;
        }));
    }

    for (const table of TABLES) {
        metrics.push(postgrestMetric("publish", "postgrest", `freshness_count:${table}`, `/freshness?table=eq.${table}`, token, (r) => {
            const rows = safeJson(r) as FreshnessRow[] | null;
            return Array.isArray(rows) ? rows.length : 0;
        }));
        metrics.push(postgrestMetric("publish", "postgrest", `freshness_all_success:${table}`, `/freshness?table=eq.${table}`, token, (r) => {
            const rows = safeJson(r) as FreshnessRow[] | null;
            return Array.isArray(rows) && rows.length > 0 && rows.every((row) => row.status === "success");
        }));
    }

    metrics.push(postgrestMetric("publish", "postgrest", `partition_count:${PARTITIONED_TABLE}`, `/freshness?table=eq.${PARTITIONED_TABLE}`, token, (r) => {
        const rows = safeJson(r) as FreshnessRow[] | null;
        if (!Array.isArray(rows)) return 0;
        return rows.filter((row) => row.partition !== null).length;
    }));

    return metrics;
}

function pollOnce(metrics: MetricRequest[]): boolean {
    const responses: K6Response[] = [];
    for (const m of metrics) {
        if (m.method === "GET") {
            responses.push(http.get(m.url, m.params) as K6Response);
        } else {
            responses.push(http.post(m.url, m.params) as K6Response);
        }
    }

    let allStreamsDrained = true;
    let allTablesPopulated = true;
    let activeRunGone = true;

    for (let i = 0; i < metrics.length; i++) {
        const m = metrics[i];
        const value = m.extract(responses[i]);
        log(m.stage, m.source, m.metric, value);

        if (m.metric.startsWith("stream_length:") && typeof value === "number" && value !== 0) {
            allStreamsDrained = false;
        }
        if (m.metric === "active_run" && value !== null) {
            activeRunGone = false;
        }
        if (m.metric.startsWith("table_row_count:") && typeof value === "number" && value === 0) {
            allTablesPopulated = false;
        }
        if (m.metric.startsWith("freshness_count:") && typeof value === "number" && value === 0) {
            allTablesPopulated = false;
        }
    }

    const complete = allStreamsDrained && allTablesPopulated && activeRunGone;

    if (complete) {
        for (let i = 0; i < metrics.length; i++) {
            const m = metrics[i];
            const value = m.extract(responses[i]);
            check(null, {
                [m.metric]: () => {
                    if (typeof value === "boolean") return value;
                    if (typeof value === "number") {
                        if (m.metric.startsWith("stream_length:")) return value === 0;
                        if (m.metric === "db_size") return value < 10;
                        if (m.metric.startsWith("table_row_count:")) return value > 0;
                        if (m.metric.startsWith("freshness_count:")) return value > 0;
                        if (m.metric === "partition_count:" + PARTITIONED_TABLE) return value === 7;
                        return true;
                    }
                    if (m.metric === "active_run") return value === null;
                    return true;
                },
            });
        }
    }

    return complete;
}

export function setup(): void {
    const k8s = new Kubernetes();
    const jobName = triggerSync(k8s);
    waitForSyncJob(k8s, jobName);
    seedAccessPolicy();
}

export default function(): void {
    const token = fetchToken();
    const metrics = buildMetrics(token);

    const deadline = Date.now() + 600_000;
    while (Date.now() < deadline) {
        const complete = pollOnce(metrics);
        if (complete) {
            log("done", "redis", "pipeline_complete", true);
            verifyNoAccess();
            break;
        }
        sleep(POLL_INTERVAL);
    }
}
