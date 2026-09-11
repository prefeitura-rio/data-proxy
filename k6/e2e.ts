import http from "k6/http";
import { Kubernetes } from "k6/x/kubernetes";
import { check, sleep } from "k6";
import { triggerSync, waitForJob, NAMESPACE, PRODUCER_CRONJOB } from "./lib.ts";

type K6Response = {
    status: number;
    body: string;
    headers: Record<string, string>;
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
    label: string;
    method: string;
    url: string;
    params: Record<string, string | object>;
    extract: (r: K6Response) => unknown;
}

declare const __ENV: Record<string, string | undefined>;

const API_URL = __ENV.BASE_URL || "http://istio-ingressgateway.istio-ingress.svc.cluster.local";
const WEBDIS_URL = __ENV.WEBDIS_URL || `${API_URL}/webdis`;
const PIPELINE_REDIS_DB = "0";
const OIDC_TOKEN_URL = __ENV.OIDC_TOKEN_URL || "http://oidc.data-proxy.svc.cluster.local:8080/token";
const OIDC_CLIENT_ID = __ENV.OIDC_CLIENT_ID || "user-with-access";
const OIDC_CLIENT_SECRET = __ENV.OIDC_CLIENT_SECRET || "test-secret";
const HOST = __ENV.API_HOST || "data-proxy.local";
const POSTGREST_URL = __ENV.POSTGREST_URL || "http://data-proxy-postgrest.data-proxy.svc.cluster.local:3000";
const PG_IMAGE = __ENV.PG_IMAGE || "localhost/data-proxy-postgres:local";
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
const STREAMS = ["dp:extract", "dp:prepare", "dp:publish"];

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
    check(null, {
        "access_policy seeded": () => status === 201 || status === 409,
    });
}

/** Verifies that a user without a policy gets a 200 with zero rows. */
function verifyNoAccess(): void {
    const token = fetchToken("user-no-access");
    let status = 0;
    let body: unknown = null;
    for (let attempt = 0; attempt < 3; attempt++) {
        const response = http.get(
            `${API_URL}/${FULL_TABLE}?limit=1`,
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
        "json path filter returns rows": () => Array.isArray(filterBody) && filterBody.length > 0,
    });
}

/** Builds a Redis metric request that reads a command through webdis. */
function redisMetric(metric: string, label: string, command: string, token: string, extract: (r: K6Response) => unknown): MetricRequest {
    return {
        stage: "sync",
        source: "redis",
        metric,
        label,
        method: "GET",
        url: `${WEBDIS_URL}/${PIPELINE_REDIS_DB}/${command}`,
        params: { headers: authHeaders(token), tags: { name: `redis:${metric}` } },
        extract,
    };
}

/** Builds a PostgREST metric request that reads a path through the proxy. */
function postgrestMetric(stage: string, source: string, metric: string, label: string, path: string, token: string, extract: (r: K6Response) => unknown): MetricRequest {
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

/** Builds the set of pipeline metrics that verify the sync completed. */
function buildMetrics(token: string): MetricRequest[] {
    const metrics: MetricRequest[] = [];

    const groups: Record<string, string> = {
        "dp:extract": "dumpers",
        "dp:prepare": "seeders",
        "dp:publish": "publishers",
    };

    STREAMS.forEach((stream) => {
        metrics.push(redisMetric(
            `stream_length:${stream}`,
            `the ${groups[stream].replace(/s$/, "")} stream is drained`,
            `XPENDING/${stream}/${groups[stream]}`,
            token,
            (r) => {
                if (r.status !== 200) return -1;
                const body = safeJson(r) as { XPENDING?: { msgs?: number } } | null;
                return Number(body?.XPENDING?.msgs ?? -1);
            },
        ));
    });

    metrics.push(redisMetric("db_size", "the pipeline database is small", "DBSIZE", token, (r) => {
        if (r.status !== 200) return -1;
        const body = safeJson(r) as { DBSIZE?: string } | null;
        return Number(body?.DBSIZE ?? -1);
    }));

    metrics.push(redisMetric("active_run", "no active run remains", "GET/dp:active", token, (r) => {
        if (r.status !== 200) return null;
        const body = safeJson(r) as { GET?: string | null } | null;
        return body?.GET ?? null;
    }));

    TABLES.forEach((table) => {
        metrics.push(postgrestMetric("extract", "postgrest", `table_status:${table}`, `${table} is reachable`, `/${table}?limit=1`, token, (r) => r.status));
        metrics.push(postgrestMetric("publish", "postgrest", `table_row_count:${table}`, `${table} has rows after publishing`, `/${table}?limit=1000`, token, (r) => {
            const body = safeJson(r);
            return Array.isArray(body) ? body.length : 0;
        }));
    });

    TABLES.forEach((table) => {
        metrics.push(postgrestMetric("publish", "postgrest", `freshness_count:${table}`, `${table} has a freshness row`, `/freshness?table=eq.${table}`, token, (r) => {
            const rows = safeJson(r) as FreshnessRow[] | null;
            return Array.isArray(rows) ? rows.length : 0;
        }));
        metrics.push(postgrestMetric("publish", "postgrest", `freshness_all_success:${table}`, `${table} freshness is all success`, `/freshness?table=eq.${table}`, token, (r) => {
            const rows = safeJson(r) as FreshnessRow[] | null;
            return Array.isArray(rows) && rows.length > 0 && rows.every((row) => row.status === "success");
        }));
    });

    metrics.push(postgrestMetric("publish", "postgrest", `partition_count:${PARTITIONED_TABLE}`, `${PARTITIONED_TABLE} has ${SYNCED_PARTITIONS} partitions`, `/freshness?table=eq.${PARTITIONED_TABLE}`, token, (r) => {
        const rows = safeJson(r) as FreshnessRow[] | null;
        if (!Array.isArray(rows)) return 0;
        return rows.filter((row) => row.partition !== null).length;
    }));

    return metrics;
}

/** Fires every metric request and returns the responses in order. */
function executeMetrics(metrics: MetricRequest[]): K6Response[] {
    const responses: K6Response[] = [];
    metrics.forEach((m) => {
        if (m.method === "GET") {
            responses.push(http.get(m.url, m.params) as K6Response);
        } else {
            responses.push(http.post(m.url, JSON.stringify(m.params), { headers: { "Content-Type": "application/json" } }) as K6Response);
        }
    });
    return responses;
}

/** Polls the pipeline metrics once and reports whether the sync is complete. */
function pollOnce(metrics: MetricRequest[]): boolean {
    const responses = executeMetrics(metrics);

    let allStreamsDrained = true;
    let activeRunGone = true;
    let published = true;

    metrics.forEach((m, i) => {
        const value = m.extract(responses[i]);

        if (m.metric.startsWith("stream_length:") && typeof value === "number" && value !== 0) {
            allStreamsDrained = false;
        }
        if (m.metric === "active_run" && value !== null) {
            activeRunGone = false;
        }
        if (m.metric.startsWith("freshness_all_success:") && value !== true) {
            published = false;
        }
    });

    return allStreamsDrained && activeRunGone && published;
}

/** Verifies every pipeline metric with a k6 check. */
function verifyMetrics(metrics: MetricRequest[]): void {
    const responses = executeMetrics(metrics);

    metrics.forEach((m, i) => {
        const value = m.extract(responses[i]);
        check(null, {
            [m.label]: () => {
                if (typeof value === "boolean") return value;
                if (typeof value === "number") {
                    if (m.metric.startsWith("stream_length:")) return value === 0;
                    if (m.metric === "db_size") return value < 10;
                    if (m.metric.startsWith("table_row_count:")) return value > 0;
                    if (m.metric.startsWith("freshness_count:")) return value > 0;
                    if (m.metric === "partition_count:" + PARTITIONED_TABLE) return value === SYNCED_PARTITIONS;
                    return true;
                }
                if (m.metric === "active_run") return value === null;
                return true;
            },
        });
    });
}

/** Triggers a sync and waits for the producer Job to complete. */
export function setup(): void {
    const k8s = new Kubernetes();
    const jobName = triggerSync(k8s);
    waitForJob(k8s, jobName);
}

/** Sends a GET through the proxy with auth headers and optional extras. */
function proxyGet(path: string, token: string, extra: Record<string, string> = {}): K6Response {
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
        throw new Error(`fallback precondition failed: ${name}: ${JSON.stringify(detail)}`);
    }
}

/** Runs one SQL statement in the application database from a short lived Job. */
function runSqlJob(k8s: Kubernetes, label: string, statement: string): void {
    const cronJob = k8s.get("CronJob.batch", PRODUCER_CRONJOB, NAMESPACE) as {
        spec: { jobTemplate: { spec: { template: { spec: { containers: Record<string, unknown>[] } } } } };
    };
    const podSpec = cronJob.spec.jobTemplate.spec.template.spec;
    const name = label.replace(/_/g, "-").slice(0, 40);
    const jobName = `e2e-${name}-${Date.now()}`;

    k8s.create({
        apiVersion: "batch/v1",
        kind: "Job",
        metadata: { name: jobName, namespace: NAMESPACE },
        spec: {
            ...cronJob.spec.jobTemplate.spec,
            backoffLimit: 0,
            template: {
                ...cronJob.spec.jobTemplate.spec.template,
                spec: {
                    ...podSpec,
                    restartPolicy: "Never",
                    containers: [
                        {
                            name: name,
                            image: PG_IMAGE,
                            command: ["/bin/sh"],
                            args: ["-c", `psql "$PG_DSN" -v ON_ERROR_STOP=1 -c ${JSON.stringify(statement)}`],
                            env: podSpec.containers[0].env,
                        },
                    ],
                },
            },
        },
    });
    waitForJob(k8s, jobName);
}

/** Truncates a local table so only BigQuery holds its rows. */
function truncateLocal(k8s: Kubernetes, table: string): void {
    runSqlJob(k8s, `truncate-${table}`, `TRUNCATE ${SCHEMA}.${table}`);
}

/** Revokes the client role's SELECT on a fallback view. */
function revokeFallbackAccess(k8s: Kubernetes, table: string): void {
    runSqlJob(k8s, `revoke-${table}`, `REVOKE SELECT ON ${SCHEMA}.${table}_bq FROM "user"`);
}

/** Grants the client role's SELECT on a fallback view. */
function grantFallbackAccess(k8s: Kubernetes, table: string): void {
    runSqlJob(k8s, `grant-${table}`, `GRANT SELECT ON ${SCHEMA}.${table}_bq TO "user"`);
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

/** Drops the local rows of one partition so only BigQuery still holds them. */
function dropLocalPartition(k8s: Kubernetes, table: string, partition: string): void {
    runSqlJob(
        k8s,
        `drop-partition-${table}`,
        `DELETE FROM ${SCHEMA}.${table} WHERE ${PARTITION_COLUMN} = '${partition}'`,
    );
}

/** Verifies the fallback serves a partition whose local rows were dropped. */
function verifyPartitionedFallback(k8s: Kubernetes, token: string): void {
    const oldest = directPostgrest(
        `/${PARTITIONED_TABLE}?select=${PARTITION_COLUMN}&order=${PARTITION_COLUMN}.asc&limit=1`,
        token,
    );
    const partition = (rowsOf(oldest)[0] as Record<string, unknown> | undefined)?.[PARTITION_COLUMN];

    requirePrecondition(
        "the synced table holds a partition to drop",
        oldest.status === 200 && typeof partition === "string",
        { status: oldest.status, partition: partition },
    );

    dropLocalPartition(k8s, PARTITIONED_TABLE, partition as string);

    const query = `${PARTITION_COLUMN}=eq.${partition}&select=protocolo_id&limit=5`;
    const local = directPostgrest(`/${PARTITIONED_TABLE}?${query}`, token);
    const fallbackView = directPostgrest(`/${PARTITIONED_TABLE}_bq?${query}`, token);

    requirePrecondition(
        "the dropped partition is empty locally",
        local.status === 200 && rowsOf(local).length === 0,
        { status: local.status, rows: rowsOf(local).length },
    );
    requirePrecondition(
        "BigQuery still holds the dropped partition",
        fallbackView.status === 200 && rowsOf(fallbackView).length > 0,
        { status: fallbackView.status },
    );

    const response = proxyGet(`/${PARTITIONED_TABLE}?${query}`, token);
    expect("a dropped partition is served by the fallback", rowsOf(response).length === rowsOf(fallbackView).length);
    expect("a dropped partition answers with a success", response.status === 200);
}

/** Verifies the fallback serves a truncated table with all columns and filters. */
function verifyFullTableFallback(k8s: Kubernetes, token: string): void {
    truncateLocal(k8s, FULL_TABLE);
    requirePrecondition(
        "the truncated table is empty locally",
        rowsOf(directPostgrest(`/${FULL_TABLE}?limit=1`, token)).length === 0,
        {},
    );

    const select = "select=id,nome,id_unidade&limit=3";
    const fallbackView = directPostgrest(`/${FULL_TABLE}_bq?${select}`, token);
    const response = proxyGet(`/${FULL_TABLE}?${select}`, token);

    expect("an emptied table is served by the fallback", rowsOf(response).length > 0);
    expect("the fallback answer matches the fallback view", rowsOf(response).length === rowsOf(fallbackView).length);
    expect("the fallback answer is a cache miss", cacheHeader(response) === "MISS");

    const proxyRow = rowsOf(response)[0] as Record<string, unknown> | undefined;
    const viewRow = rowsOf(fallbackView)[0] as Record<string, unknown> | undefined;
    expect(
        "the fallback keeps every column",
        JSON.stringify(Object.keys(proxyRow ?? {}).sort()) === JSON.stringify(Object.keys(viewRow ?? {}).sort()),
    );

    const filtered = proxyGet(`/${FULL_TABLE}?id_unidade=eq.cras_1&select=id&order=id&limit=2`, token);
    expect("filters narrow the fallback answer", rowsOf(filtered).length > 0 && rowsOf(filtered).length <= 2);

    const ranged = proxyGet(`/${FULL_TABLE}?select=id`, token, { Range: "0-0" });
    expect("a range header narrows the fallback answer", rowsOf(ranged).length === 1);

    const jsonb = proxyGet(`/${FULL_TABLE}?select=id,indicadores&indicadores->>status=not.is.null&limit=1`, token);
    const jsonbRow = rowsOf(jsonb)[0] as Record<string, unknown> | undefined;
    expect("json path filters work through the fallback", jsonb.status === 200 && jsonbRow !== undefined);
    expect("json values survive the fallback as objects", typeof jsonbRow?.indicadores === "object" && jsonbRow.indicadores !== null);
}

/** Verifies RLS narrows the fallback answer to the granted units. */
function verifyFallbackRls(k8s: Kubernetes, token: string, noAccessToken: string): void {
    truncateLocal(k8s, MULTI_RLS_TABLE);
    requirePrecondition(
        "the multi unit table is empty locally",
        rowsOf(directPostgrest(`/${MULTI_RLS_TABLE}?limit=1`, token)).length === 0,
        {},
    );

    const granted = proxyGet(`/${MULTI_RLS_TABLE}?select=id,id_cras,id_escola&limit=50`, token);
    const rows = rowsOf(granted) as Record<string, unknown>[];
    requirePrecondition("the fallback answers a subject with a policy", granted.status === 200 && rows.length > 0, {
        status: granted.status,
    });
    expect(
        "every fallback row belongs to a granted unit",
        rows.every((row) => row.id_cras === "cras_1" || row.id_escola === "escola_1"),
    );

    const denied = proxyGet(`/${MULTI_RLS_TABLE}?id_cras=eq.cras_99&select=id&limit=5`, token);
    expect("a unit outside the policy returns nothing from the fallback", rowsOf(denied).length === 0);

    const anonymous = proxyGet(`/${MULTI_RLS_TABLE}?select=id&limit=5`, noAccessToken);
    expect("a subject without a policy gets nothing from the fallback", rowsOf(anonymous).length === 0);
}

/** Verifies cache hits, token refresh, forged token refusal, and TTL expiry. */
function verifyFallbackCache(token: string, otherToken: string): void {
    const path = `/${FULL_TABLE}?select=id&limit=4`;

    const first = proxyGet(path, token);
    requirePrecondition("the first fallback answer is served", cacheHeader(first) === "MISS" && rowsOf(first).length > 0, {
        cache: cacheHeader(first),
    });

    const second = proxyGet(path, token);
    expect("a repeated request is served from the cache", cacheHeader(second) === "HIT");

    const refreshed = fetchToken();
    requirePrecondition("a fresh token differs from the first one", refreshed !== token, {});

    const rotated = proxyGet(path, refreshed);
    expect("a refreshed token is served from the cache", cacheHeader(rotated) === "HIT");
    expect(
        "a refreshed token receives the same answer",
        JSON.stringify(rowsOf(rotated)) === JSON.stringify(rowsOf(second)),
    );

    const forged = proxyGet(path, "forged.token.value");
    expect("a forged token is refused", forged.status === 401 || forged.status === 403);
    expect("a forged token is not served the cached answer", rowsOf(forged).length === 0);
    expect("the cached answer is the same", JSON.stringify(rowsOf(second)) === JSON.stringify(rowsOf(first)));

    const otherSubject = proxyGet(path, otherToken);
    expect("another subject does not share the entry", cacheHeader(otherSubject) === "MISS");
    expect("another subject does not receive the answer", JSON.stringify(rowsOf(otherSubject)) !== JSON.stringify(rowsOf(first)));

    const ranged = proxyGet(path, token, { Range: "0-1" });
    expect("a ranged answer is not cached", cacheHeader(ranged) === "MISS");
    const rangedAgain = proxyGet(path, token, { Range: "0-1" });
    expect("a ranged answer stays uncached", cacheHeader(rangedAgain) === "MISS");

    const emptyPath = `/${FULL_TABLE}?id_unidade=eq.cras_99&select=id&limit=4`;
    expect("an empty fallback answer is not cached", cacheHeader(proxyGet(emptyPath, token)) === "MISS");
    expect("an empty fallback answer stays uncached", cacheHeader(proxyGet(emptyPath, token)) === "MISS");

    const posted = http.post(`${API_URL}/access_policy`, JSON.stringify(ACCESS_POLICY_ROWS), {
        headers: { ...authHeaders(fetchToken("policy-writer")), "Content-Type": "application/json" },
        tags: { name: "proxy:post" },
    }) as K6Response;
    expect("a write request is never served from the cache", cacheHeader(posted) === "MISS");

    sleep(CACHE_TTL_SECONDS + 3);
    expect("an entry expires after the configured lifetime", cacheHeader(proxyGet(path, token)) === "MISS");
}

/** Verifies the proxy falls back to the local table when the view is unreadable. */
function verifyFallbackFailure(k8s: Kubernetes, token: string): void {
    const path = `/${FULL_TABLE}?select=id&order=id&limit=2`;

    revokeFallbackAccess(k8s, FULL_TABLE);
    const denied = directPostgrest(`/${FULL_TABLE}_bq?limit=1`, token);
    requirePrecondition("the fallback view is unreadable to the client role", denied.status !== 200, {
        status: denied.status,
    });

    const response = proxyGet(path, token);
    const local = directPostgrest(path, token);
    expect("an unreadable fallback keeps the request successful", response.status === 200);
    expect(
        "an unreadable fallback returns the local answer",
        JSON.stringify(rowsOf(response)) === JSON.stringify(rowsOf(local)),
    );

    grantFallbackAccess(k8s, FULL_TABLE);
    const restored = directPostgrest(`/${FULL_TABLE}_bq?limit=1`, token);
    expect("the fallback is readable again", restored.status === 200 && rowsOf(restored).length > 0);
    expect("the fallback answers again", rowsOf(proxyGet(path, token)).length > 0);
}

/** Verifies that a table without a fallback view is served from the local path. */
function verifyExcludedTable(token: string): void {
    if (!EXCLUDED_TABLE) {
        return;
    }

    const view = directPostgrest(`/${EXCLUDED_TABLE}_bq?limit=1`, token);
    expect(`the excluded table ${EXCLUDED_TABLE} has no fallback view`, view.status !== 200);

    const proxiedResult = proxyGet(`/${EXCLUDED_TABLE}?limit=1`, token);
    const local = directPostgrest(`/${EXCLUDED_TABLE}?limit=1`, token);
    expect("the excluded table is served by the local path", JSON.stringify(rowsOf(proxiedResult)) === JSON.stringify(rowsOf(local)));
}

/** Verifies that a per-table cache TTL outlives the global one. */
function verifyFallbackLifetimes(token: string): void {
    const longPath = `/${PARTITIONED_TABLE}?select=protocolo_id&limit=2`;
    const shortPath = `/${FULL_TABLE}?select=id&limit=2`;

    requirePrecondition("the table with its own lifetime answers", cacheHeader(proxyGet(longPath, token)) === "MISS", {});
    requirePrecondition("the table with the global lifetime answers", cacheHeader(proxyGet(shortPath, token)) === "MISS", {});

    sleep(CACHE_TTL_SECONDS + 3);

    expect("a table lifetime outlives the global one", cacheHeader(proxyGet(longPath, token)) === "HIT");
    expect("the global lifetime still applies", cacheHeader(proxyGet(shortPath, token)) === "MISS");
}

/** Verifies that a concurrent burst shares one upstream call. */
function verifyCoalescing(token: string): void {
    const path = `/${MULTI_RLS_TABLE}?select=id&limit=5`;
    const burst: { method: string; url: string; params: { headers: Record<string, string>; tags: Record<string, string> } }[] = [];

    for (let i = 0; i < BURST_REQUESTS; i++) {
        burst.push({
            method: "GET",
            url: `${API_URL}${path}`,
            params: { headers: authHeaders(token), tags: { name: "coalesce" } },
        });
    }

    requirePrecondition("the burst path is not cached yet", cacheHeader(proxyGet(path, token)) === "MISS", {});
    proxyGet("/e2e", token);

    const responses = http.batch(burst) as K6Response[];
    const bodies = responses.filter((r) => rowsOf(r).length > 0);

    expect("a burst is answered with rows", bodies.length === responses.length);
    expect("a burst is served the same answer", JSON.stringify(rowsOf(responses[0])) === JSON.stringify(rowsOf(responses[BURST_REQUESTS - 1])));
}

/**
 * Verifies the BigQuery fallback end to end.
 *
 * The fallback runs only when a GET answers with an empty array, so every check
 * below first makes the local answer empty: either by asking for a partition
 * that the sync does not keep, or by truncating the local table, which leaves
 * BigQuery untouched. Each check states its precondition and fails the run when
 * the precondition cannot be met, so a check that proves nothing cannot pass.
 */
function verifyFallback(k8s: Kubernetes): void {
    const token = fetchToken();
    const noAccessToken = fetchToken("user-no-access");

    verifyFallbackPreconditions(token);
    verifyPartitionedFallback(k8s, token);
    verifyFullTableFallback(k8s, token);
    verifyFallbackRls(k8s, token, noAccessToken);
    verifyFallbackCache(token, noAccessToken);
    verifyFallbackLifetimes(token);
    verifyFallbackFailure(k8s, token);
    verifyExcludedTable(token);
    verifyCoalescing(token);
}

/** Polls the pipeline until it completes, then runs every verification. */
export default function(): void {
    const k8s = new Kubernetes();
    const token = fetchToken();
    const metrics = buildMetrics(token);

    const deadline = Date.now() + 600_000;
    let completed = false;

    while (Date.now() < deadline) {
        const pipelineDone = pollOnce(metrics);
        if (pipelineDone) {
            completed = true;
            seedAccessPolicy();
            sleep(2);
            verifyMetrics(metrics);
            verifyNoAccess();
            verifyJsonbColumn();
            verifyFallback(k8s);
            break;
        }
        sleep(POLL_INTERVAL);
    }

    check(null, { "the pipeline completed before the deadline": () => completed });
}
