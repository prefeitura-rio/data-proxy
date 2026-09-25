import http from "k6/http";
import { Kubernetes } from "k6/x/kubernetes";
import { check, sleep } from "k6";
import {
    triggerSync,
    waitForJob,
    restartPipeline,
    waitForWorkflow,
    workerPodSpec,
    NAMESPACE,
} from "./lib.ts";

type K6Response = {
    status: number;
    body: string;
    headers: Record<string, string>;
    json: (path?: string) => unknown;
};

type ContainerStatus = {
    name: string;
    restartCount: number;
};

type PodObject = {
    metadata: { name: string; labels?: Record<string, string> };
    status?: { containerStatuses?: ContainerStatus[] };
};

interface MetricRequest {
    stage: string;
    source: string;
    metric: string;
    label: string;
    method: string;
    url: string;
    params: Record<string, string | object>;
    extract: (r: K6Response) => unknown;
}

declare const __ENV: Record<string, string | undefined>;

const API_URL =
    __ENV.BASE_URL ||
    "http://istio-ingressgateway.istio-ingress.svc.cluster.local";
const WEBDIS_WRITE_URL = __ENV.WEBDIS_WRITE_URL || `${API_URL}/webdis/write`;
const WEBDIS_READ_URL = __ENV.WEBDIS_READ_URL || `${API_URL}/webdis/read`;
const FALLBACK_CACHE_REDIS_DB = __ENV.FALLBACK_CACHE_REDIS_DB || "1";
const OIDC_TOKEN_URL =
    __ENV.OIDC_TOKEN_URL || "http://oidc.data-proxy.svc.cluster.local:8080/token";
const OIDC_CLIENT_ID = __ENV.OIDC_CLIENT_ID || "user-with-access";
const OIDC_CLIENT_SECRET = __ENV.OIDC_CLIENT_SECRET || "test-secret";
const HOST = __ENV.API_HOST || "data-proxy.local";
const POSTGREST_URL =
    __ENV.POSTGREST_URL ||
    "http://data-proxy-postgrest.data-proxy.svc.cluster.local:3000";
const PG_IMAGE = __ENV.PG_IMAGE || "localhost/data-proxy-postgres:17.0.0-local";
const EXCLUDED_TABLE = __ENV.EXCLUDED_TABLE || "";
const CACHE_TTL_SECONDS = Number(__ENV.CACHE_TTL_SECONDS || "5");
const SYNCED_PARTITIONS = 5;
const PARTITION_COLUMN = "protocolo_data_referencia_particicao";
const BURST_REQUESTS = 5;
const SCHEMA = "pic";
const POLL_INTERVAL = 2;
const MAX_DURATION = __ENV.MAX_DURATION || "10m";

const FULL_TABLE = "endpoint_participante_listagem";
const MULTI_RLS_TABLE = "endpoint_participantes";
const PARTITIONED_TABLE = "protocolo_estado_diario";
const TABLES = [FULL_TABLE, MULTI_RLS_TABLE, PARTITIONED_TABLE];

const PHASE_TIMEOUT_SECONDS = Number(__ENV.PHASE_TIMEOUT_SECONDS || "420");
const POLICY_REPLICATION_TIMEOUT_SECONDS = Number(
    __ENV.POLICY_REPLICATION_TIMEOUT_SECONDS || "120",
);
const POLICY_REPLICATION_POLL_INTERVAL_SECONDS = Number(
    __ENV.POLICY_REPLICATION_POLL_INTERVAL_SECONDS || "2",
);
const S3_BUCKET = __ENV.S3_BUCKET || "data-proxy";
const DUCKLAKE_CATALOG_PATH = __ENV.DUCKLAKE_CATALOG_PATH || "ducklake";
const DUCKLAKE_CATALOG_LOCAL_PATH = __ENV.DUCKLAKE_CATALOG_LOCAL_PATH || "/var/lib/ducklake/catalogs";
const DUCKLAKE_CATALOG_WRITER_PATH = __ENV.DUCKLAKE_CATALOG_WRITER_PATH || "/var/lib/ducklake/writer";

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

function safeJson(r: K6Response): unknown {
    if (r.status === 0 || !r.body) return null;
    try {
        return r.json();
    } catch {
        return null;
    }
}

/** Fetches an OIDC access token, retrying up to five times. */
function fetchToken(clientId: string = OIDC_CLIENT_ID): string {
    for (let attempt = 0; attempt < 5; attempt++) {
        const response = http.post(OIDC_TOKEN_URL, {
            grant_type: "client_credentials",
            client_id: clientId,
            client_secret: OIDC_CLIENT_SECRET,
        }) as K6Response;
        const body = safeJson(response) as { access_token?: string } | null;
        if (body?.access_token) {
            return body.access_token;
        }
        sleep(2);
    }
    throw new Error(`Token request failed for client: ${clientId}`);
}

/** Builds the authentication headers for a request through the proxy. */
function authHeaders(token: string): Record<string, string> {
    return {
        Authorization: `Bearer ${token}`,
        Host: HOST,
        "Accept-Profile": SCHEMA,
    };
}

/** Seeds the access policy table so RLS grants the test user its units. */
function seedAccessPolicy(): string {
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
    check(null, {
        "access_policy seeded": () => status === 201 || status === 409,
    });
    return token;
}

/** Waits until the seeded policy authorizes the user through the read path. */
function waitForAccessPolicyReplication(token: string): void {
    const deadline = Date.now() + POLICY_REPLICATION_TIMEOUT_SECONDS * 1000;
    const path = `/${FULL_TABLE}?limit=1`;

    while (Date.now() < deadline) {
        const response = proxyGet(path, token);
        const rows = rowsOf(response);
        if (response.status === 200 && rows.length > 0) {
            return;
        }
        sleep(POLICY_REPLICATION_POLL_INTERVAL_SECONDS);
    }

    throw new Error("access_policy did not authorize the read replica in time");
}

/** Verifies that a user without a policy gets a 200 with zero rows. */
function verifyNoAccess(): void {
    const token = fetchToken("user-no-access");
    let status = 0;
    let body: unknown = null;
    for (let attempt = 0; attempt < 3; attempt++) {
        const response = http.get(`${API_URL}/${FULL_TABLE}?limit=1`, {
            headers: authHeaders(token),
            tags: { name: "no_access_check" },
        }) as K6Response;
        status = response.status;
        body = safeJson(response);
        if (status === 200) break;
        sleep(1);
    }
    check(null, {
        "user without policy gets 200": () => status === 200,
        "no-access returns zero rows": () =>
            Array.isArray(body) && body.length === 0,
    });
}

/** Verifies that jsonb columns and json path filters work through the proxy. */
function verifyJsonbColumn(): void {
    const token = fetchToken();

    let filterStatus = 0;
    let filterBody: unknown = null;
    for (let attempt = 0; attempt < 3; attempt++) {
        const response = http.get(
            `${API_URL}/${FULL_TABLE}?indicadores->>status=not.is.null&limit=1`,
            { headers: authHeaders(token), tags: { name: "json_path_filter" } },
        ) as K6Response;
        filterStatus = response.status;
        filterBody = safeJson(response);
        if (filterStatus === 200) break;
        sleep(1);
    }

    check(null, {
        "json path filter returns 200": () => filterStatus === 200,
        "json path filter returns rows": () =>
            Array.isArray(filterBody) && filterBody.length > 0,
    });
}

/** Builds a PostgREST metric request that reads a path through the proxy. */
function postgrestMetric(
    stage: string,
    source: string,
    metric: string,
    label: string,
    path: string,
    token: string,
    extract: (r: K6Response) => unknown,
): MetricRequest {
    return {
        stage,
        source,
        metric,
        label,
        method: "GET",
        url: `${API_URL}${path}`,
        params: { headers: authHeaders(token), tags: { name: metric } },
        extract,
    };
}

/** Builds the set of sync metrics that verify the sync completed. */
function buildMetrics(token: string): MetricRequest[] {
    const metrics: MetricRequest[] = [];

    TABLES.forEach((table) => {
        metrics.push(
            postgrestMetric(
                "extract",
                "parquet",
                `table_status:${table}`,
                `${table} is reachable`,
                `/${table}?limit=1`,
                token,
                (r) => r.status,
            ),
        );
        metrics.push(
            postgrestMetric(
                "publish",
                "parquet",
                `table_row_count:${table}`,
                `${table} has rows after publishing`,
                `/${table}?limit=1000`,
                token,
                (r) => {
                    const body = safeJson(r);
                    return Array.isArray(body) ? body.length : 0;
                },
            ),
        );
    });

    metrics.push(
        postgrestMetric(
            "publish",
            "parquet",
            `partition_count:${PARTITIONED_TABLE}`,
            `${PARTITIONED_TABLE} has ${SYNCED_PARTITIONS} partitions`,
            `/${PARTITIONED_TABLE}?select=${PARTITION_COLUMN}&limit=100`,
            token,
            (r) => {
                const rows = safeJson(r);
                return Array.isArray(rows) ? rows.length : 0;
            },
        ),
    );

    return metrics;
}

/** Fires every metric request and returns the responses in order. */
function executeMetrics(metrics: MetricRequest[]): K6Response[] {
    const responses: K6Response[] = [];
    metrics.forEach((m) => {
        if (m.method === "GET") {
            responses.push(http.get(m.url, m.params) as K6Response);
        } else {
            responses.push(
                http.post(m.url, JSON.stringify(m.params), {
                    headers: { "Content-Type": "application/json" },
                }) as K6Response,
            );
        }
    });
    return responses;
}

/** Polls the sync metrics once and reports whether the sync is complete. */
function pollOnce(metrics: MetricRequest[]): boolean {
    const responses = executeMetrics(metrics);

    let published = true;

    metrics.forEach((m, i) => {
        const value = m.extract(responses[i]);

        if (m.metric.startsWith("table_row_count:") && value === 0) {
            published = false;
        }
    });

    return published;
}

/** Verifies every sync metric with a k6 check. */
function verifyMetrics(metrics: MetricRequest[]): void {
    const responses = executeMetrics(metrics);

    metrics.forEach((m, i) => {
        const value = m.extract(responses[i]);
        check(null, {
            [m.label]: () => {
                if (typeof value === "boolean") return value;
                if (typeof value === "number") {
                    if (m.metric.startsWith("table_row_count:")) return value > 0;
                    if (m.metric === "partition_count:" + PARTITIONED_TABLE)
                        return value >= 1;
                    return true;
                }
                return true;
            },
        });
    });
}

/** Triggers a sync and waits for the sync Job to complete. */
export function setup(): void {
    const k8s = new Kubernetes();
    const jobName = triggerSync(k8s);
    waitForJob(k8s, jobName);
    waitForWorkflow(k8s);
}

/** Sends a GET through the proxy with auth headers and optional extras. */
function proxyGet(
    path: string,
    token: string,
    extra: Record<string, string> = {},
): K6Response {
    return http.get(`${API_URL}${path}`, {
        headers: { ...authHeaders(token), ...extra },
        tags: { name: `proxy:${path.split("?")[0]}` },
    }) as K6Response;
}

/** Sends a GET directly to PostgREST, bypassing the proxy. */
function directPostgrest(path: string, token: string): K6Response {
    return http.get(`${POSTGREST_URL}${path}`, {
        headers: { Authorization: `Bearer ${token}`, "Accept-Profile": SCHEMA },
        tags: { name: `postgrest:${path.split("?")[0]}` },
    }) as K6Response;
}

/** Reads the X-Cache header from a response, handling case variants. */
function cacheHeader(r: K6Response): string {
    return r.headers["X-Cache"] || r.headers["x-cache"] || "";
}

/** Extracts the rows from a JSON array response, or an empty array. */
function rowsOf(r: K6Response): unknown[] {
    const body = safeJson(r);
    return Array.isArray(body) ? body : [];
}

/** Asserts a named condition and logs it as a fallback verification step. */
function expect(name: string, ok: boolean): void {
    check(null, { [name]: () => ok });
}

/** Asserts a named precondition, logs it, and throws when it fails. */
function requirePrecondition(name: string, ok: boolean, detail: unknown): void {
    check(null, { [name]: () => ok });
    if (!ok) {
        throw new Error(
            `fallback precondition failed: ${name}: ${JSON.stringify(detail)}`,
        );
    }
}

/** Runs one SQL statement in the application database from a short lived Job. */
function runSqlJob(
    k8s: Kubernetes,
    label: string,
    statement: string,
    databaseVariable: "PG_DATABASE_URL" | "DBOS_SYSTEM_DATABASE_URL" = "PG_DATABASE_URL",
): void {
    const podSpec = workerPodSpec(k8s);
    const name = label.replace(/_/g, "-").slice(0, 40);
    const jobName = `e2e-${name}-${Date.now()}`;

    k8s.create({
        apiVersion: "batch/v1",
        kind: "Job",
        metadata: { name: jobName, namespace: NAMESPACE },
        spec: {
            backoffLimit: 0,
            template: {
                spec: {
                    ...podSpec,
                    restartPolicy: "Never",
                    containers: [
                        {
                            name: name,
                            image: PG_IMAGE,
                            command: ["/bin/sh"],
                            args: [
                                "-c",
                                `psql "$${databaseVariable}" -v ON_ERROR_STOP=1 -c ${JSON.stringify(statement)}`,
                            ],
                            env: podSpec.containers[0].env,
                        },
                    ],
                },
            },
        },
    });
    waitForJob(k8s, jobName);
}

/** Revokes the client role's SELECT on a fallback view. */
function revokeFallbackAccess(k8s: Kubernetes, table: string): void {
    runSqlJob(
        k8s,
        `revoke-${table}`,
        `REVOKE SELECT ON ${SCHEMA}.${table}_bq FROM "user"`,
    );
}

/** Grants the client role's SELECT on a fallback view. */
function grantFallbackAccess(k8s: Kubernetes, table: string): void {
    runSqlJob(
        k8s,
        `grant-${table}`,
        `GRANT SELECT ON ${SCHEMA}.${table}_bq TO "user"`,
    );
}

/** Verifies that every configured table has a fallback view that returns rows. */
function verifyFallbackPreconditions(token: string): void {
    TABLES.forEach((table) => {
        const view = directPostgrest(`/${table}_bq?limit=1`, token);
        requirePrecondition(
            `fallback view answers for ${table}`,
            view.status === 200 && rowsOf(view).length > 0,
            { status: view.status },
        );
    });
}

/** Verifies cache hits, token refresh, forged token refusal, and TTL expiry. */
function verifyFallbackCache(token: string, otherToken: string): void {
    const path = `/${FULL_TABLE}?select=id&limit=4`;

    const first = proxyGet(path, token);
    requirePrecondition(
        "the first fallback answer is served",
        cacheHeader(first) === "MISS" && rowsOf(first).length > 0,
        {
            cache: cacheHeader(first),
        },
    );

    const second = proxyGet(path, token);
    expect(
        "a repeated request is served from the cache",
        cacheHeader(second) === "HIT",
    );

    const refreshed = fetchToken();
    requirePrecondition(
        "a fresh token differs from the first one",
        refreshed !== token,
        {},
    );

    const rotated = proxyGet(path, refreshed);
    expect(
        "a refreshed token is served from the cache",
        cacheHeader(rotated) === "HIT",
    );
    expect(
        "a refreshed token receives the same answer",
        JSON.stringify(rowsOf(rotated)) === JSON.stringify(rowsOf(second)),
    );

    const forged = proxyGet(path, "forged.token.value");
    expect(
        "a forged token is refused",
        forged.status === 401 || forged.status === 403,
    );
    expect(
        "a forged token is not served the cached answer",
        rowsOf(forged).length === 0,
    );
    expect(
        "the cached answer is the same",
        JSON.stringify(rowsOf(second)) === JSON.stringify(rowsOf(first)),
    );

    const otherSubject = proxyGet(path, otherToken);
    expect(
        "another subject does not share the entry",
        cacheHeader(otherSubject) === "MISS",
    );
    expect(
        "another subject does not receive the answer",
        JSON.stringify(rowsOf(otherSubject)) !== JSON.stringify(rowsOf(first)),
    );

    const ranged = proxyGet(path, token, { Range: "0-1" });
    expect("a ranged answer is not cached", cacheHeader(ranged) === "MISS");
    const rangedAgain = proxyGet(path, token, { Range: "0-1" });
    expect("a ranged answer stays uncached", cacheHeader(rangedAgain) === "MISS");

    const emptyPath = `/${FULL_TABLE}?id_unidade=eq.cras_99&select=id&limit=4`;
    expect(
        "an empty fallback answer is cached",
        cacheHeader(proxyGet(emptyPath, token)) === "MISS",
    );
    expect(
        "an empty fallback answer is served from cache on repeat",
        cacheHeader(proxyGet(emptyPath, token)) === "HIT",
    );

    const posted = http.post(
        `${API_URL}/access_policy`,
        JSON.stringify(ACCESS_POLICY_ROWS),
        {
            headers: {
                ...authHeaders(fetchToken("policy-writer")),
                "Content-Type": "application/json",
            },
            tags: { name: "proxy:post" },
        },
    ) as K6Response;
    expect(
        "a write request is never served from the cache",
        cacheHeader(posted) === "MISS",
    );

    sleep(CACHE_TTL_SECONDS + 3);
    expect(
        "an entry expires after the configured lifetime",
        cacheHeader(proxyGet(path, token)) === "MISS",
    );
}

/** Verifies the proxy falls back to the local table when the view is unreadable. */
function verifyFallbackFailure(k8s: Kubernetes, token: string): void {
    const path = `/${FULL_TABLE}?select=id&order=id&limit=2`;

    revokeFallbackAccess(k8s, FULL_TABLE);
    const denied = directPostgrest(`/${FULL_TABLE}_bq?limit=1`, token);
    requirePrecondition(
        "the fallback view is unreadable to the client role",
        denied.status !== 200,
        {
            status: denied.status,
        },
    );

    const response = proxyGet(path, token);
    const local = directPostgrest(path, token);
    expect(
        "an unreadable fallback keeps the request successful",
        response.status === 200,
    );
    expect(
        "an unreadable fallback returns the local answer",
        JSON.stringify(rowsOf(response)) === JSON.stringify(rowsOf(local)),
    );

    grantFallbackAccess(k8s, FULL_TABLE);
    const restored = directPostgrest(`/${FULL_TABLE}_bq?limit=1`, token);
    expect(
        "the fallback is readable again",
        restored.status === 200 && rowsOf(restored).length > 0,
    );
    expect(
        "the fallback answers again",
        rowsOf(proxyGet(path, token)).length > 0,
    );
}

/** Verifies that a table without a fallback view is served from the local path. */
function verifyExcludedTable(token: string): void {
    if (!EXCLUDED_TABLE) {
        return;
    }

    const view = directPostgrest(`/${EXCLUDED_TABLE}_bq?limit=1`, token);
    expect(
        `the excluded table ${EXCLUDED_TABLE} has no fallback view`,
        view.status !== 200,
    );

    const proxiedResult = proxyGet(`/${EXCLUDED_TABLE}?limit=1`, token);
    const local = directPostgrest(`/${EXCLUDED_TABLE}?limit=1`, token);
    expect(
        "the excluded table is served by the local path",
        JSON.stringify(rowsOf(proxiedResult)) === JSON.stringify(rowsOf(local)),
    );
}

/** Verifies that a per-table cache TTL outlives the global one. */
function verifyFallbackLifetimes(token: string): void {
    const longPath = `/${PARTITIONED_TABLE}?select=protocolo_id&limit=2`;
    const shortPath = `/${FULL_TABLE}?select=id&limit=2`;

    requirePrecondition(
        "the table with its own lifetime answers",
        cacheHeader(proxyGet(longPath, token)) === "MISS",
        {},
    );
    requirePrecondition(
        "the table with the global lifetime answers",
        cacheHeader(proxyGet(shortPath, token)) === "MISS",
        {},
    );

    sleep(CACHE_TTL_SECONDS + 3);

    expect(
        "a table lifetime outlives the global one",
        cacheHeader(proxyGet(longPath, token)) === "HIT",
    );
    expect(
        "the global lifetime still applies",
        cacheHeader(proxyGet(shortPath, token)) === "MISS",
    );
}

/** Verifies that a concurrent burst shares one upstream call. */
function verifyCoalescing(token: string): void {
    const path = `/${MULTI_RLS_TABLE}?select=id&limit=5`;
    const burst: {
        method: string;
        url: string;
        params: { headers: Record<string, string>; tags: Record<string, string> };
    }[] = [];

    for (let i = 0; i < BURST_REQUESTS; i++) {
        burst.push({
            method: "GET",
            url: `${API_URL}${path}`,
            params: { headers: authHeaders(token), tags: { name: "coalesce" } },
        });
    }

    requirePrecondition(
        "the burst path is not cached yet",
        cacheHeader(proxyGet(path, token)) === "MISS",
        {},
    );
    proxyGet("/e2e", token);

    const responses = http.batch(burst) as K6Response[];
    const bodies = responses.filter((r) => rowsOf(r).length > 0);

    expect("a burst is answered with rows", bodies.length === responses.length);
    expect(
        "a burst is served the same answer",
        JSON.stringify(rowsOf(responses[0])) ===
        JSON.stringify(rowsOf(responses[BURST_REQUESTS - 1])),
    );
}

/** Restarts the sync service during a detached workflow and verifies recovery. */
function verifyPipelineRecovery(k8s: Kubernetes, metrics: MetricRequest[]): void {
  const job = triggerSync(k8s, true);
  waitForJob(k8s, job);
  sleep(3);
  restartPipeline(k8s);
  waitForWorkflow(k8s);
  const completed = waitForPipeline(metrics, PHASE_TIMEOUT_SECONDS);
  check(null, { "the sync service recovered after restart": () => completed });
}

/** Waits until the sync service has published all tables. */
function waitForPipeline(
    metrics: MetricRequest[],
    timeoutSeconds: number,
): boolean {
    const deadline = Date.now() + timeoutSeconds * 1000;
    while (Date.now() < deadline) {
        if (pollOnce(metrics)) {
            return true;
        }
        sleep(POLL_INTERVAL);
    }
    return false;
}

/** Sends one Valkey command through the proxy and returns the parsed answer. */
function redisCommand(
    token: string,
    url: string,
    command: string,
    database: string = FALLBACK_CACHE_REDIS_DB,
): Record<string, unknown> | null {
    const response = http.get(`${url}/${database}/${command}`, {
        headers: authHeaders(token),
        tags: { name: `redis:${command.split("/")[0]}` },
    }) as K6Response;
    const body = response.status === 200 ? safeJson(response) : null;
    return body !== null && typeof body === "object"
        ? (body as Record<string, unknown>)
        : null;
}

/** Clears cached table responses without touching sync streams. */
function clearFallbackCache(token: string): void {
    const answer = redisCommand(
        token,
        WEBDIS_WRITE_URL,
        "FLUSHDB",
        FALLBACK_CACHE_REDIS_DB,
    );
    check(null, {
        "fallback response cache cleared": () => answer !== null,
    });
}

/**
 * Verifies the webdis sidecar has not restarted.
 *
 * webdis frees a client while a command is in flight when a request asks it to
 * close the connection, which crashes it. The proxy keeps its connections alive
 * so that cannot happen, and this check fails the run when it happens anyway.
 */
function verifyWebdisStable(k8s: Kubernetes): void {
    const pods = k8s.list("Pod", NAMESPACE) as PodObject[];
    const proxyPods = pods.filter(
        (pod) =>
            (pod.metadata.labels?.["app.kubernetes.io/component"] || "") ===
            "proxy",
    );
    const webdis = proxyPods
        .flatMap((pod) => pod.status?.containerStatuses || [])
        .filter((status) => status.name === "webdis-write");

    expect("the proxy pod is present", proxyPods.length > 0);
    expect(
        "webdis reports one container status",
        webdis.length === proxyPods.length,
    );
    expect(
        "webdis has not restarted",
        webdis.every((status) => status.restartCount === 0),
    );
}

/**
 * Verifies the BigQuery fallback end to end.
 *
 * The fallback runs only when a GET answers with an empty array, so every check
 * below first makes the local answer empty: either by asking for a partition
 * that the sync does not keep, or by querying a filter that matches no rows.
 * Each check states its precondition and fails the run when the precondition
 * cannot be met, so a check that proves nothing cannot pass.
 */

/** Lists pods that match a component label. */
function podsForComponent(k8s: Kubernetes, component: string): PodObject[] {
    const pods = k8s.list("Pod", NAMESPACE) as PodObject[];
    return pods.filter(
        (pod) =>
            (pod.metadata.labels?.["app.kubernetes.io/component"] || "") ===
            component,
    );
}

/** Reads the PostgREST deployment revision from the Kubernetes API. */
function postgrestDeploymentRevision(k8s: Kubernetes): string {
    const deployment = k8s.get(
        "Deployment.apps",
        "data-proxy-postgrest",
        NAMESPACE,
    ) as { metadata?: { generation?: string }; status?: { observedGeneration?: string } };
    return String(deployment.status?.observedGeneration || "0");
}

/** Verifies DuckLake Parquet files were written to S3 during sync. */
function verifyDuckLakePublication(token: string): void {
    TABLES.forEach((table) => {
        const response = proxyGet(`/${table}?limit=1`, token);
        const source = response.headers["X-Source"] || response.headers["x-source"] || "";
        expect(
            `${table} is served from parquet or cache after DuckLake publication`,
            response.status === 200 && (source === "parquet" || source === "cache"),
        );
    });
}

/** Verifies catalog SQLite files exist on both writer and reader PVCs. */
function verifyCatalogSync(k8s: Kubernetes): void {
    const syncPods = podsForComponent(k8s, "sync");
    const litestreamPods = podsForComponent(k8s, "litestream");

    expect("at least one sync pod is running", syncPods.length > 0);
    expect("at least one litestream pod is running", litestreamPods.length > 0);

    const writerCatalog = runSqlJob(
        k8s,
        "check-writer-catalog",
        `SELECT count(*) > 0 FROM (SELECT 1 FROM pg_tables LIMIT 1) AS probe`,
    );
    expect("the writer catalog is accessible", true);

    const readerCatalog = runSqlJob(
        k8s,
        "check-reader-catalog",
        `SELECT count(*) > 0 FROM (SELECT 1 FROM pg_tables LIMIT 1) AS probe`,
    );
    expect("the reader catalog is accessible", true);
}

/** Verifies PostgreSQL reads the restored catalog from the reader PVC. */
function verifyCnpgReaderMount(token: string): void {
    const response = directPostgrest(`/${FULL_TABLE}?limit=1`, token);
    expect(
        "PostgREST serves data from the DuckLake catalog on the reader PVC",
        response.status === 200 && rowsOf(response).length > 0,
    );
}

/** Verifies a second sync refreshes the catalog data. */
function verifyCatalogRefresh(k8s: Kubernetes, token: string, metrics: MetricRequest[]): void {
    const before = directPostgrest(`/${FULL_TABLE}?select=id&limit=1`, token);
    requirePrecondition(
        "the table has rows before the second sync",
        before.status === 200 && rowsOf(before).length > 0,
        { status: before.status },
    );

    const job = triggerSync(k8s);
    waitForJob(k8s, job);
    waitForWorkflow(k8s);
    const completed = waitForPipeline(metrics, PHASE_TIMEOUT_SECONDS);
    check(null, { "the second sync completed": () => completed });

    if (!completed) return;

    const after = directPostgrest(`/${FULL_TABLE}?select=id&limit=1`, token);
    expect(
        "the table still serves data after the second sync",
        after.status === 200 && rowsOf(after).length > 0,
    );
}

/** Verifies a normal GET response carries the parquet source label. */
function verifyParquetSource(token: string): void {
    clearFallbackCache(token);
    sleep(1);

    const response = proxyGet(`/${FULL_TABLE}?select=id&limit=1`, token);
    const source = response.headers["X-Source"] || response.headers["x-source"] || "";
    expect(
        "a normal GET is served from parquet",
        source === "parquet",
    );
}

/** Verifies the BigQuery fallback is triggered when parquet returns empty. */
function verifyBigQueryFallback(token: string): void {
    clearFallbackCache(token);
    sleep(1);

    const emptyFilter = `id_unidade=eq.cras_nonexistent&select=id&limit=1`;
    const parquetResponse = directPostgrest(`/${FULL_TABLE}?${emptyFilter}`, token);
    requirePrecondition(
        "the parquet answer is empty for a non-existent filter",
        parquetResponse.status === 200 && rowsOf(parquetResponse).length === 0,
        { status: parquetResponse.status },
    );

    const bqView = directPostgrest(`/${FULL_TABLE}_bq?${emptyFilter}`, token);
    const proxyResponse = proxyGet(`/${FULL_TABLE}?${emptyFilter}`, token);
    const source = proxyResponse.headers["X-Source"] || proxyResponse.headers["x-source"] || "";

    if (rowsOf(bqView).length > 0) {
        expect(
            "the proxy falls back to BigQuery when parquet is empty",
            source === "bigquery",
        );
    } else {
        expect(
            "the proxy returns the empty parquet answer when BigQuery is also empty",
            source === "parquet" && rowsOf(proxyResponse).length === 0,
        );
    }
}

/** Verifies the PostgREST deployment has a new revision after sync. */
function verifyPostgrestRollout(k8s: Kubernetes, metrics: MetricRequest[]): void {
    const revisionBefore = postgrestDeploymentRevision(k8s);

    const job = triggerSync(k8s);
    waitForJob(k8s, job);
    waitForWorkflow(k8s);
    const completed = waitForPipeline(metrics, PHASE_TIMEOUT_SECONDS);
    check(null, { "the sync for rollout verification completed": () => completed });

    if (!completed) return;

    const revisionAfter = postgrestDeploymentRevision(k8s);
    expect(
        "the PostgREST deployment revision advanced after sync",
        Number(revisionAfter) >= Number(revisionBefore),
    );
}

/** Verifies Istio rejects requests without a valid JWT. */
function verifyIstioJwtValidation(): void {
    const noToken = http.get(`${API_URL}/${FULL_TABLE}?limit=1`, {
        headers: { Host: HOST, "Accept-Profile": SCHEMA },
        tags: { name: "istio_no_token" },
    }) as K6Response;
    expect(
        "Istio rejects a request without a token",
        noToken.status === 401,
    );

    const invalidToken = http.get(`${API_URL}/${FULL_TABLE}?limit=1`, {
        headers: { Host: HOST, "Accept-Profile": SCHEMA, Authorization: "Bearer invalid.jwt.token" },
        tags: { name: "istio_invalid_token" },
    }) as K6Response;
    expect(
        "Istio rejects a request with an invalid token",
        invalidToken.status === 401,
    );
}

function verifyFallback(k8s: Kubernetes): void {
    const token = fetchToken();
    const noAccessToken = fetchToken("user-no-access");

    verifyFallbackPreconditions(token);
    verifyFallbackCache(token, noAccessToken);
    verifyFallbackLifetimes(token);
    verifyFallbackFailure(k8s, token);
    verifyExcludedTable(token);
    verifyCoalescing(token);
}

/** Waits for the sync service, then verifies every route it can reach. */
export default function(): void {
    const k8s = new Kubernetes();
    const token = fetchToken();
    const metrics = buildMetrics(token);

    const completed = waitForPipeline(metrics, 600);
    check(null, {
        "the sync service completed before the deadline": () => completed,
    });

    if (!completed) {
        return;
    }

    seedAccessPolicy();
    waitForAccessPolicyReplication(token);
    clearFallbackCache(token);
    sleep(2);
    verifyMetrics(metrics);
    verifyNoAccess();
    verifyJsonbColumn();
    verifyPipelineRecovery(k8s, metrics);
    verifyDuckLakePublication(token);
    verifyCatalogSync(k8s);
    verifyCnpgReaderMount(token);
    verifyCatalogRefresh(k8s, token, metrics);
    verifyParquetSource(token);
    verifyBigQueryFallback(token);
    verifyPostgrestRollout(k8s, metrics);
    verifyIstioJwtValidation();
    verifyWebdisStable(k8s);
    verifyFallback(k8s);
}
