import http from "k6/http";
import type { RequestParams, Response as K6Response } from "k6/http";
import { Kubernetes } from "k6/x/kubernetes";
import type { KubernetesPodSpec } from "k6/x/kubernetes";
import { check, sleep } from "k6";
import {
    triggerSync,
    waitForJob,
    syncPods,
    cronJobPodSpec,
    waitForWorkflow,
    workerPodSpec,
    deploymentPodSpec,
    runCronJob,
    NAMESPACE,
} from "./lib.ts";

type ContainerStatus = {
    name: string;
    restartCount: number;
};

type PodObject = {
    metadata: { name: string; labels?: Record<string, string> };
    status?: { containerStatuses?: ContainerStatus[] };
};

interface MetricRequest {
    metric: string;
    label: string;
    url: string;
    params: RequestParams;
    extract: (response: K6Response) => unknown;
}

declare const __ENV: Record<string, string | undefined>;

const API_URL =
    __ENV.BASE_URL ||
    "http://istio-ingressgateway.istio-ingress.svc.cluster.local";
const WEBDIS_WRITE_URL = __ENV.WEBDIS_WRITE_URL || `${API_URL}/webdis/write`;
const PROXY_CACHE_REDIS_DB = __ENV.PROXY_CACHE_REDIS_DB || "1";
const OIDC_TOKEN_URL =
    __ENV.OIDC_TOKEN_URL ||
    "http://keycloak.keycloak.svc.cluster.local:8080/realms/dev/protocol/openid-connect/token";
const OIDC_USER_CLIENT_ID = __ENV.OIDC_USER_CLIENT_ID || "user";
const OIDC_NO_POLICY_CLIENT_ID = __ENV.OIDC_NO_POLICY_CLIENT_ID || "no_policy";
const OIDC_CLIENT_SECRET = __ENV.OIDC_CLIENT_SECRET || "test-secret";
const HOST = __ENV.API_HOST || "data-proxy.local";
const POSTGREST_URL =
    __ENV.POSTGREST_URL ||
    "http://data-proxy-postgrest.data-proxy.svc.cluster.local:3000";
const POSTGREST_READ_URL =
    __ENV.POSTGREST_READ_URL ||
    "http://data-proxy-postgrest-ro.data-proxy.svc.cluster.local:3000";
const PG_IMAGE = __ENV.PG_IMAGE || "localhost/data-proxy-postgres:17.0.0-local";
const PARTITIONED_SOURCE =
    __ENV.PARTITIONED_SOURCE ||
    "rj-ia-desenvolvimento.dev.partitioned_table";
const CACHE_TTL_SECONDS = Number(__ENV.CACHE_TTL_SECONDS || "5");
const SYNCED_PARTITIONS = 4;
const PARTITION_COLUMN = "date";
const BURST_REQUESTS = 5;
const SCHEMA = "test";
const MAINTENANCE_CRONJOB = __ENV.MAINTENANCE_CRONJOB || "data-proxy-maintenance";
const BACKUP_CRONJOB = __ENV.BACKUP_CRONJOB || `data-proxy-${SCHEMA}-backup`;
const STALE_VIEW = `${SCHEMA}.e2e_stale_view`;
const REMOVED_PARTITION = "19990101";
const STATE_DATABASE = "DBOS_SYSTEM_DATABASE_URL";
const POLL_INTERVAL = 2;
const TOKEN_REFRESH_SECONDS = 60;
const MAX_DURATION = __ENV.MAX_DURATION || "10m";
const SUITE = __ENV.SUITE || "full";
const MODE = __ENV.MODE || "single";
if (["e2e", "ha", "full"].indexOf(SUITE) === -1) {
    throw new Error(`SUITE must be e2e, ha, or full: ${SUITE}`);
}
if (["single", "ha"].indexOf(MODE) === -1) {
    throw new Error(`MODE must be single or ha: ${MODE}`);
}
const RUN_E2E = SUITE !== "ha";
const RUN_MODES = SUITE !== "e2e";
const EXPECT_HA = MODE === "ha";
const MODE_TIMEOUT_SECONDS = Number(__ENV.MODE_TIMEOUT_SECONDS || "900");
const CLUSTER_NAME = __ENV.CLUSTER_NAME || "data-proxy";
const HA_MIN_INSTANCES = 2;
const SECRETS_MOUNT = "/var/lib/postgresql/data/.duckdb/stored_secrets";
const SECRETS_SUBPATH = "duckdb-secrets";
const READ_POSTGREST = "data-proxy-postgrest-ro";
const READ_POOLER = "data-proxy-pooler-ro";
const CLUSTER_SCALER = "data-proxy-cluster-autoscaler";
const PROXY_DEPLOYMENT = "data-proxy-proxy";
const PAUSED_REPLICAS = "autoscaling.keda.sh/paused-replicas";
const PARALLEL_READS = 10;

const FULL_TABLE = "full_table";
const MULTI_RLS_TABLE = "multi_rls_table";
const PARTITIONED_TABLE = "partitioned_table";
const TABLES = [FULL_TABLE, MULTI_RLS_TABLE, PARTITIONED_TABLE];
const BIG_TABLE = "big_table";
const FAILING_TABLE = "failing_table";
const FAILING_SOURCE = "rj-ia-desenvolvimento.dev.failing_table";

const PHASE_TIMEOUT_SECONDS = Number(__ENV.PHASE_TIMEOUT_SECONDS || "420");
const POLICY_REPLICATION_TIMEOUT_SECONDS = Number(
    __ENV.POLICY_REPLICATION_TIMEOUT_SECONDS || "120",
);
const POLICY_REPLICATION_POLL_INTERVAL_SECONDS = Number(
    __ENV.POLICY_REPLICATION_POLL_INTERVAL_SECONDS || "2",
);
const DUCKLAKE_CATALOG_LOCAL_PATH =
    __ENV.DUCKLAKE_CATALOG_LOCAL_PATH || "/var/lib/ducklake/catalogs";

const ACCESS_POLICY_ROWS = [
    {
        subject: "test_user_1",
        unit_type: "unit",
        unit_id: "unit_1",
    },
    {
        subject: "test_user_1",
        unit_type: "region",
        unit_id: "region_1",
    },
    {
        subject: "test_user_1",
        unit_type: "group",
        unit_id: "group_1",
    },
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
    setupTimeout: "15m",
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
function requestToken(clientId: string): { token: string; lifetime: number } {
    for (let attempt = 0; attempt < 5; attempt++) {
        const response = http.post(OIDC_TOKEN_URL, {
            grant_type: "client_credentials",
            client_id: clientId,
            client_secret: OIDC_CLIENT_SECRET,
        }) as K6Response;
        const body = safeJson(response) as {
            access_token?: string;
            expires_in?: number;
        } | null;
        if (body?.access_token) {
            return { token: body.access_token, lifetime: body.expires_in ?? 0 };
        }
        sleep(2);
    }
    throw new Error(`Token request failed for client: ${clientId}`);
}

function fetchToken(clientId: string = OIDC_USER_CLIENT_ID): string {
    return requestToken(clientId).token;
}

let userSession = { token: "", expiresAt: 0 };

/** Returns a token for the test user, refreshed before it expires. */
function userToken(): string {
    if (Date.now() >= userSession.expiresAt) {
        const { token, lifetime } = requestToken(OIDC_USER_CLIENT_ID);
        userSession = {
            token,
            expiresAt: Date.now() + (lifetime - TOKEN_REFRESH_SECONDS) * 1000,
        };
    }
    return userSession.token;
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
    const token = fetchToken("policy_writer");
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
    const token = fetchToken(OIDC_NO_POLICY_CLIENT_ID);
    let status = 0;
    let body: unknown = null;
    let source = "";
    let snapshot = "";
    for (let attempt = 0; attempt < 3; attempt++) {
        const response = http.get(`${API_URL}/${FULL_TABLE}?limit=1`, {
            headers: authHeaders(token),
            tags: { name: "no_access_check" },
        }) as K6Response;
        status = response.status;
        body = safeJson(response);
        source = sourceHeader(response);
        snapshot = snapshotHeader(response);
        if (status === 200) break;
        sleep(1);
    }
    check(null, {
        "user without policy gets 200": () => status === 200,
        "no-access returns zero rows": () =>
            Array.isArray(body) && body.length === 0,
        "no-access queries no source": () => source === "",
        "no-access response omits the DuckLake snapshot": () => snapshot === "",
    });

    const partitioned = proxyGet(`/${PARTITIONED_TABLE}?limit=1`, token);
    expect(
        "no-access partitioned request returns zero rows",
        partitioned.status === 200 && rowsOf(partitioned).length === 0,
    );
    expect(
        "no-access partitioned request queries no source",
        sourceHeader(partitioned) === "",
    );
    expect(
        "no-access partitioned response omits the DuckLake snapshot",
        snapshotHeader(partitioned) === "",
    );
}

/** Verifies that an authorized user cannot see rows from another unit. */
function verifyAuthorizedRows(token: string): void {
    const response = proxyGet(
        `/${FULL_TABLE}?select=unit_id&limit=100`,
        token,
    );
    const rows = rowsOf(response) as Array<Record<string, unknown>>;
    expect(
        "authorized user receives rows",
        response.status === 200 && rows.length > 0,
    );
    expect(
        "authorized user receives only permitted units",
        rows.every((row) => row.unit_id === "unit_1"),
    );

    const multi = proxyGet(
        `/${MULTI_RLS_TABLE}?select=region_id,group_id&limit=100`,
        token,
    );
    const multiRows = rowsOf(multi) as Array<Record<string, unknown>>;
    expect(
        "multi-RLS table returns rows",
        multi.status === 200 && multiRows.length > 0,
    );
    expect(
        "multi-RLS rows match an authorized unit",
        multiRows.every(
            (row) => row.region_id === "region_1" || row.group_id === "group_1",
        ),
    );
}

/** Verifies that jsonb columns and json path filters work through the proxy. */
function verifyJsonbColumn(): void {
    const token = fetchToken();

    let filterStatus = 0;
    let filterBody: unknown = null;
    for (let attempt = 0; attempt < 3; attempt++) {
        const response = http.get(
            `${API_URL}/${FULL_TABLE}?metadata->>status=not.is.null&limit=1`,
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
    metric: string,
    label: string,
    path: string,
    token: string,
    extract: (response: K6Response) => unknown,
): MetricRequest {
    return {
        metric,
        label,
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
                `table_status:${table}`,
                `${table} is reachable`,
                `/${table}?limit=1`,
                token,
                (response) => response.status,
            ),
        );
        metrics.push(
            postgrestMetric(
                `table_row_count:${table}`,
                `${table} has rows after publishing`,
                `/${table}?limit=1000`,
                token,
                (response) => {
                    const body = safeJson(response);
                    return Array.isArray(body) ? body.length : 0;
                },
            ),
        );
    });

    metrics.push(
        postgrestMetric(
            `partition_count:${PARTITIONED_TABLE}`,
            `${PARTITIONED_TABLE} has ${SYNCED_PARTITIONS} partitions`,
            `/${PARTITIONED_TABLE}?select=${PARTITION_COLUMN}&limit=100`,
            token,
            (response) => {
                const rows = safeJson(response);
                return Array.isArray(rows) ? rows.length : 0;
            },
        ),
    );

    return metrics;
}

/** Fires every metric request and returns the responses in order. */
function executeMetrics(metrics: MetricRequest[]): K6Response[] {
    return metrics.map((metric) => http.get(metric.url, metric.params));
}

/** Polls the sync metrics once and reports whether the sync is complete. */
function pollOnce(metrics: MetricRequest[]): boolean {
    const responses = executeMetrics(metrics);

    let published = true;

    metrics.forEach((m, i) => {
        const value = m.extract(responses[i]);

        if (m.metric.startsWith("table_status:") && value !== 200) {
            published = false;
        }
        if (m.metric.startsWith("table_row_count:") && value === 0) {
            published = false;
        }
        if (
            m.metric === "partition_count:" + PARTITIONED_TABLE &&
            (typeof value !== "number" || value < SYNCED_PARTITIONS)
        ) {
            published = false;
        }
    });

    return published;
}

/** Verifies every sync metric with a k6 check. */
function verifyMetrics(): void {
    const metrics = buildMetrics(userToken());
    const responses = executeMetrics(metrics);

    metrics.forEach((m, i) => {
        const value = m.extract(responses[i]);
        check(null, {
            [m.label]: () => {
                if (m.metric.startsWith("table_status:")) return value === 200;
                if (m.metric.startsWith("table_row_count:")) {
                    return typeof value === "number" && value > 0;
                }
                if (m.metric === "partition_count:" + PARTITIONED_TABLE) {
                    return typeof value === "number" && value >= SYNCED_PARTITIONS;
                }
                return false;
            },
        });
    });
}

/** Verifies that an unchanged source does not change published state. */
function verifyBlockedTable(k8s: Kubernetes): void {
    const failing = `fields->>'table' = '${FAILING_SOURCE}'`;
    runSqlJob(
        k8s,
        "clear-blocked-errors",
        `DELETE FROM data_proxy.errors WHERE ${failing}`,
        "DBOS_SYSTEM_DATABASE_URL",
    );
    const snapshotBefore = snapshotValue(userToken());

    const job = triggerSync(k8s);
    waitForJob(k8s, job);

    for (const reason of ["extraction_failed", "table_blocked"]) {
        checkSql(
            k8s,
            `check-${reason}`,
            `SELECT count(*) > 0 FROM data_proxy.errors WHERE reason = '${reason}' AND ${failing}`,
            "t",
            "DBOS_SYSTEM_DATABASE_URL",
        );
    }
    checkSql(
        k8s,
        "check-blocked-state",
        `SELECT count(*) FILTER (WHERE table_name = '${FAILING_SOURCE}') || '/' || count(*) FROM data_proxy.state`,
        `0/${[...TABLES, BIG_TABLE].length}`,
        "DBOS_SYSTEM_DATABASE_URL",
    );

    const blocked = http.get(`${API_URL}/${FAILING_TABLE}?limit=1`, {
        headers: authHeaders(userToken()),
        tags: { name: "blocked_table" },
    }) as K6Response;
    const served = http.get(`${API_URL}/${FULL_TABLE}?limit=1`, {
        headers: authHeaders(userToken()),
        tags: { name: "blocked_table_neighbour" },
    }) as K6Response;
    expect("a table that failed extraction is not found", blocked.status === 404);
    expect("a table next to a failed one is still served", served.status === 200);
    expect(
        "a failed extraction does not change the snapshot",
        snapshotValue(userToken()) === snapshotBefore,
    );
}

function verifyNoChangeRun(k8s: Kubernetes): void {
    const snapshotBefore = snapshotValue(userToken());
    const revisionBefore = postgrestDeploymentRevision(k8s);
    const job = triggerSync(k8s);
    waitForJob(k8s, job);

    const snapshotAfter = snapshotValue(userToken());
    const revisionAfter = postgrestDeploymentRevision(k8s);
    expect("an unchanged sync preserves the snapshot", snapshotAfter === snapshotBefore);
    expect(
        "an unchanged sync does not restart PostgREST",
        revisionAfter === revisionBefore,
    );
}

/** Triggers a sync and waits for the sync Job to complete. */
export function setup(): void {
    const k8s = new Kubernetes();
    const jobName = triggerSync(k8s);
    waitForJob(k8s, jobName);
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
function directPostgrest(
    path: string,
    token: string,
    extra: Record<string, string> = {},
    baseUrl: string = POSTGREST_URL,
): K6Response {
    return http.get(`${baseUrl}${path}`, {
        headers: {
            Authorization: `Bearer ${token}`,
            "Accept-Profile": SCHEMA,
            ...extra,
        },
        tags: { name: `postgrest:${path.split("?")[0]}` },
    }) as K6Response;
}

/** Reads one response header, handling case variants. */
function headerOf(r: K6Response, name: string): string {
    const wanted = name.toLowerCase();
    const key = Object.keys(r.headers).find((k) => k.toLowerCase() === wanted);
    return key ? r.headers[key] : "";
}

/** Reads the X-Cache header from a response. */
function cacheHeader(r: K6Response): string {
    return headerOf(r, "X-Cache");
}

/** Reads the sources that served a response, from the X-Source header. */
function sourceHeader(r: K6Response): string {
    return headerOf(r, "X-Source");
}

/** Reads the DuckLake snapshot a response was read from. */
function snapshotHeader(r: K6Response): string {
    return headerOf(r, "X-DuckLake-Snapshot");
}

/** Extracts the rows from a JSON array response, or an empty array. */
function rowsOf(r: K6Response): unknown[] {
    const body = safeJson(r);
    return Array.isArray(body) ? body : [];
}

/** Reads the current DuckLake snapshot through the exposed RPC. */
function snapshotValue(token: string): string {
    const response = directPostgrest(
        "/rpc/ducklake_latest_snapshot",
        token,
    );
    const body = safeJson(response);
    requirePrecondition(
        "the current DuckLake snapshot is readable",
        response.status === 200 && body !== null,
        { status: response.status },
    );
    return JSON.stringify(body);
}

/** Asserts a named condition and logs it as a verification step. */
function expect(name: string, ok: boolean): void {
    check(null, { [name]: () => ok });
}

/** Asserts a named precondition, logs it, and throws when it fails. */
function requirePrecondition(name: string, ok: boolean, detail: unknown): void {
    check(null, { [name]: () => ok });
    if (!ok) {
        throw new Error(
            `precondition failed: ${name}: ${JSON.stringify(detail)}`,
        );
    }
}

/** Where a command Job runs: its pod spec, image, and service account. */
interface CommandJobOptions {
    podSpec?: KubernetesPodSpec;
    image?: string;
    serviceAccountName?: string;
}

/** Runs one shell command from a short-lived Job and fails when it fails. */
function runCommandJob(
    k8s: Kubernetes,
    label: string,
    command: string,
    options: CommandJobOptions = {},
): void {
    const podSpec = options.podSpec ?? workerPodSpec(k8s);
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
                    ...(options.serviceAccountName
                        ? { serviceAccountName: options.serviceAccountName }
                        : {}),
                    restartPolicy: "Never",
                    containers: [
                        {
                            name: name,
                            image: options.image ?? PG_IMAGE,
                            command: ["/bin/sh"],
                            args: ["-c", command],
                            env: podSpec.containers[0].env,
                            volumeMounts: podSpec.containers[0].volumeMounts,
                        },
                    ],
                },
            },
        },
    });
    waitForJob(k8s, jobName);
}

/** Shell test that succeeds when one SQL query prints the expected value. */
function sqlEquals(
    query: string,
    expected: string,
    databaseVariable: "PG_DATABASE_URL" | "DBOS_SYSTEM_DATABASE_URL" = "PG_DATABASE_URL",
): string {
    return `test "$(psql "$${databaseVariable}" -tAc ${JSON.stringify(query)})" = ${JSON.stringify(expected)}`;
}

/** Fails the run unless one SQL query prints the expected value. */
function checkSql(
    k8s: Kubernetes,
    label: string,
    query: string,
    expected: string,
    databaseVariable: "PG_DATABASE_URL" | "DBOS_SYSTEM_DATABASE_URL" = "PG_DATABASE_URL",
): void {
    runCommandJob(k8s, label, sqlEquals(query, expected, databaseVariable));
}

/** Runs one SQL statement in a database from a short-lived Job. */
function runSqlJob(
    k8s: Kubernetes,
    label: string,
    statement: string,
    databaseVariable: "PG_DATABASE_URL" | "DBOS_SYSTEM_DATABASE_URL" = "PG_DATABASE_URL",
): void {
    runCommandJob(
        k8s,
        label,
        `psql "$${databaseVariable}" -v ON_ERROR_STOP=1 -c ${JSON.stringify(statement)}`,
    );
}

/** Verifies cache hits, token refresh, forged token refusal, and TTL expiry. */
function verifyProxyCache(token: string, otherToken: string): void {
    const path = `/${FULL_TABLE}?select=id&limit=4`;

    const first = proxyGet(path, token);
    requirePrecondition(
        "the first answer is served",
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

    const emptyPath = `/${FULL_TABLE}?unit_id=eq.unit_99&select=id&limit=4`;
    expect(
        "an empty answer is not cached",
        cacheHeader(proxyGet(emptyPath, token)) === "MISS",
    );
    expect(
        "an empty answer stays uncached on repeat",
        cacheHeader(proxyGet(emptyPath, token)) === "MISS",
    );
    expect(
        "a cache hit keeps the source of the stored answer",
        sourceHeader(proxyGet(path, token)) === sourceHeader(first),
    );

    const posted = http.post(
        `${API_URL}/access_policy`,
        JSON.stringify(ACCESS_POLICY_ROWS),
        {
            headers: {
                ...authHeaders(fetchToken("policy_writer")),
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

/** Verifies that a per-table cache TTL outlives the global one. */
function verifyProxyLifetimes(token: string): void {
    const longPath = `/${PARTITIONED_TABLE}?select=id&limit=2`;
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

/**
 * Kills the sync pod that owns a running workflow and verifies distributed recovery.
 *
 * A normal pod deletion lets the workflow finish during the shutdown grace
 * period, so the pod is deleted with no grace period. The recovery loop must
 * then re-enqueue the workflow for a pod that is still alive.
 */
function verifyPipelineRecovery(k8s: Kubernetes): void {
    runSqlJob(k8s, "prepare-recovery", "DELETE FROM data_proxy.state", STATE_DATABASE);
    const podsBefore = syncPods(k8s).map((pod) => pod.metadata.uid);

    waitForJob(k8s, triggerSync(k8s, true));
    killWorkflowOwner(k8s);
    waitForWorkflow(k8s);
    const completed = waitForPipeline(PHASE_TIMEOUT_SECONDS);
    check(null, { "the sync service recovered after restart": () => completed });

    const live = syncPods(k8s).map((pod) => pod.metadata.uid);
    expect(
        "the pod that owned the workflow is gone",
        podsBefore.filter((uid) => !live.includes(uid)).length === 1,
    );
    checkSql(
        k8s,
        "check-recovery-executor",
        `SELECT executor_id IN (${live.map((uid) => `'${uid}'`).join(", ")}) FROM dbos.workflow_status WHERE name = 'run_sync' ORDER BY created_at DESC LIMIT 1`,
        "t",
        STATE_DATABASE,
    );
    expect("a pod that is still alive finished the recovered workflow", true);
}

/** Force-deletes the sync pod that executes the running sync workflow. */
function killWorkflowOwner(k8s: Kubernetes): void {
    const image = cronJobPodSpec(k8s, MAINTENANCE_CRONJOB).containers[0].image;
    const owner =
        "SELECT executor_id FROM dbos.workflow_status WHERE name = 'run_sync' AND status = 'PENDING' ORDER BY created_at DESC LIMIT 1";
    const script = [
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
        serviceAccountName: "data-proxy-k6",
    });
}

/** Waits until the sync service has published all tables. */
function waitForPipeline(timeoutSeconds: number): boolean {
    const deadline = Date.now() + timeoutSeconds * 1000;
    while (Date.now() < deadline) {
        if (pollOnce(buildMetrics(userToken()))) {
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
    database: string = PROXY_CACHE_REDIS_DB,
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
function clearProxyCache(token: string): void {
    const answer = redisCommand(
        token,
        WEBDIS_WRITE_URL,
        "FLUSHDB",
        PROXY_CACHE_REDIS_DB,
    );
    check(null, {
        "proxy response cache cleared": () => answer !== null,
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

/** Verifies every table is served by the sources its configuration allows. */
function verifyDuckLakePublication(token: string): void {
    const expected: Record<string, string> = {
        [FULL_TABLE]: "ducklake",
        [MULTI_RLS_TABLE]: "ducklake",
        [PARTITIONED_TABLE]: "ducklake+bigquery",
    };

    TABLES.forEach((table) => {
        const response = proxyGet(`/${table}?limit=1`, token);
        expect(
            `${table} is served by ${expected[table]} after DuckLake publication`,
            response.status === 200 && sourceHeader(response) === expected[table],
        );
        expect(
            `${table} reports the DuckLake snapshot it read`,
            /^[0-9]+$/.test(snapshotHeader(response)),
        );
    });
}

/** Verifies catalog SQLite files exist on both writer and reader PVCs. */
function verifyCatalogSync(k8s: Kubernetes): void {
    const syncPods = podsForComponent(k8s, "sync");
    const litestreamPods = podsForComponent(k8s, "litestream");

    expect("at least one sync pod is running", syncPods.length > 0);
    expect("at least one litestream pod is running", litestreamPods.length > 0);

    runCommandJob(
        k8s,
        "check-writer-catalog",
        `test -s ${DUCKLAKE_CATALOG_LOCAL_PATH}/${SCHEMA}/catalog.sqlite`,
    );
    expect("the writer catalog is present", true);

    runCommandJob(
        k8s,
        "check-reader-catalog",
        `test -s ${DUCKLAKE_CATALOG_LOCAL_PATH}/${SCHEMA}/catalog.sqlite`,
        { podSpec: deploymentPodSpec(k8s, "data-proxy-litestream") },
    );
    expect("the reader catalog is present", true);
}

/** Verifies the change feed returns only the changes a user may see. */
function verifyChangeFeed(token: string): void {
    const latest = Number(snapshotValue(token));
    const noPolicyToken = fetchToken(OIDC_NO_POLICY_CLIENT_ID);
    const changeTypes = ["insert", "delete", "update_preimage", "update_postimage"];
    const feeds = [
        {
            table: FULL_TABLE,
            columns: "unit_id",
            permitted: (row: Record<string, unknown>) => row.unit_id === "unit_1",
        },
        {
            table: MULTI_RLS_TABLE,
            columns: "region_id,group_id",
            permitted: (row: Record<string, unknown>) =>
                row.region_id === "region_1" || row.group_id === "group_1",
        },
    ];

    for (const feed of feeds) {
        const path = `/rpc/ducklake_changes_${feed.table}?select=change_snapshot_id,change_type,${feed.columns}&start_snapshot=0`;
        const all = proxyGet(path, token);
        const rows = rowsOf(all) as Array<Record<string, unknown>>;
        const bounded = proxyGet(`${path}&end_snapshot=${latest}`, token);
        const denied = proxyGet(path, noPolicyToken);

        expect(
            `${feed.table} change feed returns rows`,
            all.status === 200 && rows.length > 0,
        );
        expect(
            `${feed.table} change feed returns only permitted units`,
            rows.every(feed.permitted),
        );
        expect(
            `${feed.table} change feed names a known change type`,
            rows.every((row) => changeTypes.includes(String(row.change_type))),
        );
        expect(
            `${feed.table} change feed stays within the published snapshots`,
            rows.every((row) => Number(row.change_snapshot_id) <= latest),
        );
        expect(
            `${feed.table} change feed ends at the latest snapshot by default`,
            bounded.status === 200 && rowsOf(bounded).length === rows.length,
        );
        expect(
            `${feed.table} change feed is empty for a user without a policy`,
            denied.status === 200 && rowsOf(denied).length === 0,
        );
    }
}

/**
 * Replays each kind of partition change by editing the stored manifest.
 *
 * The next sync must repair the manifest and leave the DuckLake rows unchanged,
 * because publication deletes the rows a changed partition replaces before it
 * inserts them again.
 */
function verifyPartitionChanges(k8s: Kubernetes): void {
    const pinned = () => ({ "X-DuckLake-Snapshot": snapshotValue(userToken()) });
    const baseline = rowsPerPartition(userToken(), pinned()).counts;
    requirePrecondition(
        "the partitioned table has a manifest to replay",
        baseline.size === SYNCED_PARTITIONS,
        { partitions: baseline.size },
    );

    const table = `table_name = '${PARTITIONED_SOURCE}'`;
    const oldest = "(SELECT min(key) FROM jsonb_each(state->'partitions'))";
    const cases = [
        {
            id: "added",
            name: "a partition missing from the manifest is added again",
            edit: `UPDATE data_proxy.state SET state = state #- ARRAY['partitions', ${oldest}] WHERE ${table}`,
            query: `SELECT count(*) FROM data_proxy.state, jsonb_object_keys(state->'partitions') WHERE ${table}`,
            broken: String(SYNCED_PARTITIONS - 1),
            healthy: String(SYNCED_PARTITIONS),
            publishes: true,
        },
        {
            id: "updated",
            name: "a partition with a stale signature is updated",
            edit: `UPDATE data_proxy.state SET state = jsonb_set(state, ARRAY['partitions', ${oldest}, 'signature'], to_jsonb('stale'::text)) WHERE ${table}`,
            query: `SELECT count(*) FROM data_proxy.state, jsonb_each(state->'partitions') WHERE ${table} AND value->>'signature' = 'stale'`,
            broken: "1",
            healthy: "0",
            publishes: true,
        },
        {
            id: "removed",
            name: "a partition that left the source is removed",
            edit: `UPDATE data_proxy.state SET state = jsonb_set(state, ARRAY['partitions', '${REMOVED_PARTITION}'], (SELECT value || jsonb_build_object('partition_id', '${REMOVED_PARTITION}', 'selection', (value->'selection') || jsonb_build_object('lower', '1999-01-01', 'upper', '1999-01-02')) FROM jsonb_each(state->'partitions') ORDER BY key LIMIT 1)) WHERE ${table}`,
            query: `SELECT count(*) FROM data_proxy.state WHERE ${table} AND (state->'partitions'->'${REMOVED_PARTITION}') IS NOT NULL`,
            broken: "1",
            healthy: "0",
            publishes: false,
        },
    ];

    for (const change of cases) {
        const before = Number(snapshotValue(userToken()));

        runSqlJob(k8s, `edit-${change.id}`, change.edit, STATE_DATABASE);
        checkSql(k8s, `check-${change.id}-edit`, change.query, change.broken, STATE_DATABASE);

        waitForJob(k8s, triggerSync(k8s));

        checkSql(k8s, `check-${change.id}-fix`, change.query, change.healthy, STATE_DATABASE);
        const after = rowsPerPartition(userToken(), pinned()).counts;
        expect(
            `${change.name}: the manifest is repaired`,
            true,
        );
        expect(
            `${change.name}: the DuckLake rows are unchanged`,
            after.size === baseline.size &&
            [...baseline].every(([date, rows]) => after.get(date) === rows),
        );
        if (change.publishes) {
            expect(
                `${change.name}: a new snapshot is published`,
                Number(snapshotValue(userToken())) > before,
            );
        }
    }
}

/** Shell prefix that points rclone at the backup store and today's backup folder. */
const BACKUP_STORE = [
    "export RCLONE_CONFIG=/dev/null RCLONE_CONFIG_STORE_TYPE=s3 RCLONE_CONFIG_STORE_PROVIDER=Other",
    'RCLONE_CONFIG_STORE_ENDPOINT="$S3_ENDPOINT" RCLONE_CONFIG_STORE_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID"',
    'RCLONE_CONFIG_STORE_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" RCLONE_CONFIG_STORE_FORCE_PATH_STYLE=true',
    "RCLONE_CONFIG_STORE_REGION=auto RCLONE_CONFIG_STORE_NO_CHECK_BUCKET=true",
].join(" ");

/** Verifies the deployed backup CronJob uploads its dumps and prunes the access log. */
function verifyBackupJob(k8s: Kubernetes): void {
    const podSpec = cronJobPodSpec(k8s, BACKUP_CRONJOB);
    const image = podSpec.containers[0].image;
    const folder = `dir="store:$S3_BUCKET/$BACKUP_PREFIX/${SCHEMA}/$(date -u +%F)"`;

    runSqlJob(
        k8s,
        "create-stale-log",
        `INSERT INTO ${SCHEMA}.access_log (subject, unit_type, unit_id, action, changed_at) VALUES ('e2e_stale', 'unit', 'unit_9', 'delete', now() - interval '365 days')`,
    );
    runCommandJob(
        k8s,
        "reset-backup-folder",
        `${BACKUP_STORE}; ${folder}; rclone purge "$dir" || true`,
        { podSpec, image },
    );

    waitForJob(k8s, runCronJob(k8s, BACKUP_CRONJOB));
    expect("the backup CronJob completes", true);

    runCommandJob(
        k8s,
        "check-backup-objects",
        `${BACKUP_STORE}; ${folder}; test "$(rclone lsf "$dir" | sort | tr '\\n' ' ')" = "access_log.dump access_policy.dump catalog.sqlite "`,
        { podSpec, image },
    );
    expect("the backup uploads the state dumps and the catalog", true);

    checkSql(
        k8s,
        "check-pruned-log",
        `SELECT count(*) FROM ${SCHEMA}.access_log WHERE subject = 'e2e_stale'`,
        "0",
    );
    expect("the backup prunes access-log rows past the retention window", true);
}

/** Verifies the deployed maintenance CronJob drops a view that left the sync config. */
function verifyMaintenanceJob(k8s: Kubernetes): void {
    runSqlJob(
        k8s,
        "create-stale-view",
        `CREATE OR REPLACE VIEW ${STALE_VIEW} AS SELECT 1 AS id`,
    );

    waitForJob(k8s, runCronJob(k8s, MAINTENANCE_CRONJOB));
    expect("the maintenance CronJob completes", true);

    runCommandJob(
        k8s,
        "check-stale-view",
        `test "$(psql "$PG_DATABASE_URL" -tAc "SELECT to_regclass('${STALE_VIEW}') IS NULL")" = t`,
    );
    expect("the maintenance CronJob drops views missing from the sync config", true);
}

/** Verifies PostgreSQL reads the restored catalog from the reader PVC. */
function verifyCnpgReaderMount(token: string): void {
    const response = directPostgrest(`/${FULL_TABLE}?limit=1`, token);
    expect(
        "PostgREST serves data from the DuckLake catalog on the reader PVC",
        response.status === 200 && rowsOf(response).length > 0,
    );
}

/** Verifies a table without fallbacks reports DuckLake as its only source. */
function verifyDuckLakeSource(token: string): void {
    clearProxyCache(token);
    sleep(1);

    const response = proxyGet(`/${FULL_TABLE}?select=id&limit=1`, token);
    expect(
        "a table without fallbacks is served from DuckLake only",
        response.status === 200 && sourceHeader(response) === "ducklake",
    );
}

/** Verifies partitions outside DuckLake are served from BigQuery. */
function verifyBigQueryRows(token: string): void {
    clearProxyCache(token);
    sleep(1);

    const pinned = { "X-DuckLake-Snapshot": snapshotValue(token) };
    const localOldest = directPostgrest(
        `/${PARTITIONED_TABLE}?select=${PARTITION_COLUMN}&order=${PARTITION_COLUMN}.asc&limit=1`,
        token,
        pinned,
    );
    const localRows = rowsOf(localOldest);
    requirePrecondition(
        "the local partitioned table has an oldest partition",
        localOldest.status === 200 && localRows.length > 0,
        { status: localOldest.status },
    );

    const oldest = String(
        (localRows[0] as Record<string, unknown>)[PARTITION_COLUMN] || "",
    );
    const filter =
        `${PARTITION_COLUMN}=lt.${oldest}&select=id,${PARTITION_COLUMN},unit_id&limit=20`;
    const local = directPostgrest(
        `/${PARTITIONED_TABLE}?${filter}`,
        token,
        pinned,
    );
    requirePrecondition(
        "an older partition is absent from DuckLake",
        local.status === 200 && rowsOf(local).length === 0,
        { status: local.status, oldest },
    );

    const response = proxyGet(`/${PARTITIONED_TABLE}?${filter}`, token);
    const rows = rowsOf(response) as Array<Record<string, unknown>>;
    expect(
        "an older partition is served from BigQuery",
        response.status === 200 && sourceHeader(response) === "ducklake+bigquery",
    );
    expect(
        "the BigQuery rows belong to the older partition",
        rows.length > 0 &&
        rows.every((row) => String(row[PARTITION_COLUMN]) < oldest),
    );
    expect(
        "BigQuery rows obey RLS",
        rows.length > 0 && rows.every((row) => row.unit_id === "unit_1"),
    );
}

/** Reads the number of rows per partition through the proxy. */
function rowsPerPartition(
    token: string,
    extra: Record<string, string> = {},
): { response: K6Response; counts: Map<string, number> } {
    const response = proxyGet(
        `/${PARTITIONED_TABLE}?select=${PARTITION_COLUMN},count()&order=${PARTITION_COLUMN}.asc`,
        token,
        extra,
    );
    const counts = new Map<string, number>();
    (rowsOf(response) as Array<Record<string, unknown>>).forEach((row) => {
        counts.set(String(row[PARTITION_COLUMN]), Number(row.count));
    });
    return { response, counts };
}

/**
 * Verifies a table split in half between DuckLake and BigQuery.
 *
 * The seeded table has twice as many partitions as the sync keeps. DuckLake
 * holds the newest half and BigQuery serves the older half.
 */
function verifySplitSources(token: string): void {
    clearProxyCache(token);
    sleep(1);

    const all = rowsPerPartition(token);
    const ducklake = rowsPerPartition(token, {
        "X-DuckLake-Snapshot": snapshotValue(token),
    });
    const allDates = [...all.counts.keys()].sort();
    const ducklakeDates = [...ducklake.counts.keys()].sort();
    const bigqueryDates = allDates.filter((date) => !ducklake.counts.has(date));

    expect(
        "the split table is served by DuckLake and BigQuery",
        all.response.status === 200 &&
        sourceHeader(all.response) === "ducklake+bigquery",
    );
    expect(
        "the pinned split table is served by DuckLake only",
        ducklake.response.status === 200 &&
        sourceHeader(ducklake.response) === "ducklake",
    );
    requirePrecondition(
        "the split table has partitions",
        allDates.length > 0 && ducklakeDates.length > 0,
        { all: allDates.length, ducklake: ducklakeDates.length },
    );
    expect(
        "DuckLake holds half of the partitions",
        ducklakeDates.length * 2 === allDates.length,
    );
    expect(
        "BigQuery serves the other half of the partitions",
        bigqueryDates.length === ducklakeDates.length,
    );
    expect(
        "DuckLake holds the newest partitions",
        bigqueryDates.every((date) => date < ducklakeDates[0]),
    );
    expect(
        "the combined answer keeps the DuckLake rows unchanged",
        ducklakeDates.every((date) => all.counts.get(date) === ducklake.counts.get(date)),
    );
    expect(
        "the BigQuery partitions have rows",
        bigqueryDates.every((date) => (all.counts.get(date) || 0) > 0),
    );
}

/** Verifies a client can pin a DuckLake snapshot version. */
function verifySnapshotVersion(token: string): void {
    const path = `/${FULL_TABLE}?select=id&order=id&limit=5`;
    const latest = proxyGet(path, token);
    const snapshot = snapshotHeader(latest);
    requirePrecondition(
        "the latest answer names its snapshot",
        latest.status === 200 && /^[0-9]+$/.test(snapshot),
        { status: latest.status, snapshot },
    );

    const pinned = proxyGet(path, token, { "X-DuckLake-Snapshot": snapshot });
    expect("a pinned version is served", pinned.status === 200);
    expect(
        "a pinned version is read from DuckLake only",
        sourceHeader(pinned) === "ducklake",
    );
    expect(
        "a pinned version reports that version",
        snapshotHeader(pinned) === snapshot,
    );
    expect(
        "the latest version returns the same rows as the default request",
        JSON.stringify(rowsOf(pinned)) === JSON.stringify(rowsOf(latest)),
    );

    const unknown = proxyGet(path, token, {
        "X-DuckLake-Snapshot": String(Number(snapshot) + 1000),
    });
    expect("an unknown version is not found", unknown.status === 404);

    const invalid = proxyGet(path, token, { "X-DuckLake-Snapshot": "latest" });
    expect("a version that is not a number is rejected", invalid.status === 400);
}

/** Verifies Istio rejects requests without a valid JWT. */
function verifyIstioJwtValidation(): void {
    const noToken = http.get(`${API_URL}/${FULL_TABLE}?limit=1`, {
        headers: { Host: HOST, "Accept-Profile": SCHEMA },
        tags: { name: "istio_no_token" },
    }) as K6Response;
    expect(
        "Istio rejects a request without a token",
        noToken.status === 403,
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

/** Verifies the proxy cache, its lifetimes, and request coalescing. */
function verifyProxy(): void {
    const token = fetchToken();
    const noAccessToken = fetchToken(OIDC_NO_POLICY_CLIENT_ID);

    verifyProxyCache(token, noAccessToken);
    verifyProxyLifetimes(token);
    verifyCoalescing(token);
}

/** Waits for the sync service, then verifies every route it can reach. */
interface NamedObject {
    metadata: { name: string; annotations?: Record<string, string> };
    status?: {
        readyInstances?: number;
        phase?: string;
        availableReplicas?: number;
        conditions?: { type: string; status: string }[];
    };
}

interface ClusterObject extends NamedObject {
    spec: { instances: number };
}

interface PostgresPod {
    metadata: { labels?: Record<string, string> };
    spec: {
        containers: {
            volumeMounts?: { mountPath: string; subPath?: string }[];
        }[];
    };
}

/** Polls until the probe holds or the timeout passes. */
function waitUntil(timeoutSeconds: number, probe: () => boolean): boolean {
    const deadline = Date.now() + timeoutSeconds * 1000;
    while (Date.now() < deadline) {
        if (probe()) return true;
        sleep(POLL_INTERVAL);
    }
    return probe();
}

function namedObjects(k8s: Kubernetes, kind: string): NamedObject[] {
    return k8s.list(kind, NAMESPACE) as NamedObject[];
}

function findObject(
    k8s: Kubernetes,
    kind: string,
    name: string,
): NamedObject | undefined {
    return namedObjects(k8s, kind).find((item) => item.metadata.name === name);
}

function clusterObject(k8s: Kubernetes): ClusterObject {
    return k8s.get(
        "Cluster.postgresql.cnpg.io",
        CLUSTER_NAME,
        NAMESPACE,
    ) as ClusterObject;
}

/** Reports whether the Cluster has the instance count of the mode and all are ready. */
function clusterSettled(k8s: Kubernetes, ha: boolean): boolean {
    const cluster = clusterObject(k8s);
    const instances = cluster.spec.instances;
    const sized = ha ? instances >= HA_MIN_INSTANCES : instances === 1;
    return (
        sized &&
        cluster.status?.readyInstances === instances &&
        cluster.status?.phase === "Cluster in healthy state"
    );
}

/** Reports whether the read PostgREST and the read pooler exist exactly in HA mode. */
function readSideMatches(k8s: Kubernetes, ha: boolean): boolean {
    const postgrest = findObject(k8s, "Deployment.apps", READ_POSTGREST);
    const poolerDeployment = findObject(k8s, "Deployment.apps", READ_POOLER);
    const pooler = findObject(k8s, "Pooler.postgresql.cnpg.io", READ_POOLER);
    const present = [postgrest, poolerDeployment, pooler].every(Boolean);
    const absent = [postgrest, poolerDeployment, pooler].every((item) => !item);
    return ha ? present : absent;
}

function readSideAvailable(k8s: Kubernetes): boolean {
    return [READ_POSTGREST, READ_POOLER].every(
        (name) =>
            (findObject(k8s, "Deployment.apps", name)?.status
                ?.availableReplicas ?? 0) >= 1,
    );
}

/** Reports whether the cluster scaler autoscales in HA mode and pins the count in single mode. */
function clusterScalerMatches(k8s: Kubernetes, ha: boolean): boolean {
    const scaler = findObject(k8s, "ScaledObject.keda.sh", CLUSTER_SCALER);
    const pinned = scaler?.metadata.annotations?.[PAUSED_REPLICAS];
    return scaler !== undefined && (ha ? pinned === undefined : pinned === "1");
}

function proxyHaMode(k8s: Kubernetes): string {
    const env = deploymentPodSpec(k8s, PROXY_DEPLOYMENT).containers.flatMap(
        (container) => container.env ?? [],
    );
    return env.find((entry) => entry.name === "HA_MODE")?.value ?? "";
}

/** Reports whether every PostgreSQL pod mounts the shared S3 secret folder. */
function postgresPodsMountSecret(k8s: Kubernetes, expected: number): boolean {
    const pods = (k8s.list("Pod", NAMESPACE) as PostgresPod[]).filter(
        (pod) =>
            pod.metadata.labels?.["cnpg.io/cluster"] === CLUSTER_NAME &&
            pod.metadata.labels?.["cnpg.io/podRole"] === "instance",
    );
    return (
        pods.length === expected &&
        pods.every((pod) =>
            pod.spec.containers.some((container) =>
                (container.volumeMounts ?? []).some(
                    (mount) =>
                        mount.mountPath === SECRETS_MOUNT &&
                        mount.subPath === SECRETS_SUBPATH,
                ),
            ),
        )
    );
}

/** Shell test that succeeds when one SQL query on the read pooler prints the expected value. */
function readPoolerSqlEquals(query: string, expected: string): string {
    const dsn = `$(echo "$PG_DATABASE_URL" | sed 's/-rw:/-pooler-ro:/')`;
    return `test "$(psql "${dsn}" -tAc ${JSON.stringify(query)})" = ${JSON.stringify(expected)}`;
}

/** Sends parallel uncached reads, so the pool of the read PostgREST opens several connections. */
function uncachedReads(
    baseUrl: string,
    headers: Record<string, string>,
): K6Response[] {
    const seed = Date.now();
    return http.batch(
        Array.from({ length: PARALLEL_READS }, (_, index) => ({
            method: "GET" as const,
            url: `${baseUrl}/${FULL_TABLE}?select=id&limit=${100 + ((seed + index) % 800)}`,
            params: { headers, tags: { name: "mode_uncached_read" } },
        })),
    ) as K6Response[];
}

/** Verifies the PostgreSQL side that only HA mode has: standbys, streaming, and read routing. */
function verifyStandbys(k8s: Kubernetes): void {
    checkSql(
        k8s,
        "ha-streaming-replicas",
        `SELECT count(*) >= ${HA_MIN_INSTANCES - 1} FROM pg_stat_replication WHERE state = 'streaming'`,
        "t",
    );
    runCommandJob(
        k8s,
        "ha-read-pooler-on-standby",
        readPoolerSqlEquals("SELECT pg_is_in_recovery()", "t"),
    );
    checkSql(k8s, "ha-write-pooler-on-primary", "SELECT pg_is_in_recovery()", "f");
}

/** Verifies that reads reach the standbys and that only the write side takes writes. */
function verifyReadWriteSplit(): void {
    const reads = uncachedReads(POSTGREST_READ_URL, {
        Authorization: `Bearer ${userToken()}`,
        "Accept-Profile": SCHEMA,
    });
    expect(
        "HA: every parallel read on the read PostgREST succeeds from a standby",
        reads.every(
            (response) =>
                response.status === 200 &&
                sourceHeader(response).indexOf("ducklake") !== -1,
        ),
    );

    const write = http.post(
        `${POSTGREST_READ_URL}/access_policy`,
        JSON.stringify(ACCESS_POLICY_ROWS),
        {
            headers: {
                Authorization: `Bearer ${fetchToken("policy_writer")}`,
                "Accept-Profile": SCHEMA,
                "Content-Profile": SCHEMA,
                "Content-Type": "application/json",
                Prefer: "resolution=merge-duplicates",
            },
            tags: { name: "mode_write_on_read_postgrest" },
        },
    ) as K6Response;
    expect("HA: the read PostgREST refuses a write", write.status >= 400);
}

/** Verifies the deployed topology and the read and write paths of one mode. */
function verifyMode(k8s: Kubernetes, ha: boolean): void {
    const mode = ha ? "HA" : "single";
    const timeout = MODE_TIMEOUT_SECONDS;

    expect(
        `${mode}: the cluster settles on the instances of the mode`,
        waitUntil(timeout, () => clusterSettled(k8s, ha)),
    );
    expect(
        `${mode}: the read PostgREST and read pooler ${ha ? "exist" : "are gone"}`,
        waitUntil(timeout, () => readSideMatches(k8s, ha)),
    );
    if (ha) {
        expect(
            "HA: the read PostgREST and read pooler are available",
            waitUntil(timeout, () => readSideAvailable(k8s)),
        );
    }
    expect(
        `${mode}: the cluster scaler ${ha ? "autoscales" : "pins the count"}`,
        waitUntil(timeout, () => clusterScalerMatches(k8s, ha)),
    );
    expect(
        `${mode}: the proxy knows the mode`,
        proxyHaMode(k8s) === String(ha),
    );
    expect(
        `${mode}: every PostgreSQL pod mounts the shared S3 secret`,
        postgresPodsMountSecret(k8s, clusterObject(k8s).spec.instances),
    );
    checkSql(
        k8s,
        "mode-state-intact",
        "SELECT count(*) FROM data_proxy.state",
        String([...TABLES, BIG_TABLE].length),
        "DBOS_SYSTEM_DATABASE_URL",
    );
    if (ha) verifyStandbys(k8s);

    seedAccessPolicy();
    waitForAccessPolicyReplication(userToken());
    const read = uncachedReads(API_URL, authHeaders(userToken()));
    expect(
        `${mode}: reads through the proxy return rows from DuckLake`,
        read.every(
            (response) =>
                response.status === 200 &&
                rowsOf(response).length > 0 &&
                sourceHeader(response).indexOf("ducklake") !== -1,
        ),
    );
    if (ha) verifyReadWriteSplit();
}

function verifyEndToEnd(k8s: Kubernetes): void {
    seedAccessPolicy();

    const completed = waitForPipeline(600);
    check(null, {
        "the sync service completed before the deadline": () => completed,
    });

    if (!completed) {
        return;
    }

    waitForAccessPolicyReplication(userToken());
    verifyNoChangeRun(k8s);
    clearProxyCache(userToken());
    sleep(2);
    verifyMetrics();
    verifyNoAccess();
    verifyAuthorizedRows(userToken());
    verifyJsonbColumn();
    verifyBlockedTable(k8s);
    verifyPipelineRecovery(k8s);
    verifyDuckLakePublication(userToken());
    verifyChangeFeed(userToken());
    verifyCatalogSync(k8s);
    verifyMaintenanceJob(k8s);
    verifyBackupJob(k8s);
    verifyCnpgReaderMount(userToken());
    verifyDuckLakeSource(userToken());
    verifyBigQueryRows(userToken());
    verifySplitSources(userToken());
    verifySnapshotVersion(userToken());
    verifyPartitionChanges(k8s);
    verifyIstioJwtValidation();
    verifyWebdisStable(k8s);
    verifyProxy();
}

export default function(): void {
    const k8s = new Kubernetes();
    try {
        if (RUN_E2E) verifyEndToEnd(k8s);
        if (RUN_MODES) verifyMode(k8s, EXPECT_HA);
    } catch (error) {
        expect("the suite ends without an exception", false);
        throw error;
    }
}
