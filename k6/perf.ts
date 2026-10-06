import http from "k6/http";
import type { RequestParams, Response as K6Response } from "k6/http";
import { check, sleep } from "k6";
import { Kubernetes } from "k6/x/kubernetes";
import { Counter, Rate, Trend } from "k6/metrics";
import { NAMESPACE } from "./lib.ts";

declare const __ENV: Record<string, string | undefined>;

type TokenData = { token: string; expiresAt: number };
type SetupData = {
    tokens: TokenData[];
    bigqueryDates: string[];
    ids: string[];
    snapshots: string[];
    replicaBaseline: Record<string, number>;
};
type TokenResponse = { access_token?: string; expires_in?: number };
type Route = {
    profile: string;
    path: string;
    name: string;
    expectedSource: "ducklake" | "ducklake+bigquery";
    heavy?: boolean;
    weight: number;
    clients: string[];
    checkBody: (body: unknown) => boolean;
};
type Stage = { target: number; duration: string };
type Profile = {
    executor: "ramping-vus" | "ramping-arrival-rate";
    stages?: Stage[];
    duration?: string;
    startRate?: number;
    timeUnit?: string;
    preAllocatedVUs?: number;
    maxVUs?: number;
};
type ScaleDeployment = {
    metadata: { name: string; labels?: Record<string, string> };
    spec: { replicas?: number };
    status?: { availableReplicas?: number };
};

const API_URL =
    __ENV.BASE_URL ||
    "http://istio-ingressgateway.istio-ingress.svc.cluster.local";
const OIDC_TOKEN_URL =
    __ENV.OIDC_TOKEN_URL ||
    "http://keycloak.keycloak.svc.cluster.local:8080/realms/dev/protocol/openid-connect/token";
const OIDC_USER_CLIENT_ID = __ENV.OIDC_USER_CLIENT_ID || "user";
const OIDC_NO_POLICY_CLIENT_ID =
    __ENV.OIDC_NO_POLICY_CLIENT_ID || "no_policy";
const OIDC_POLICY_WRITER_CLIENT_ID =
    __ENV.OIDC_POLICY_WRITER_CLIENT_ID || "policy_writer";
const OIDC_CLIENT_SECRET = __ENV.OIDC_CLIENT_SECRET || "test-secret";
const HOST = __ENV.API_HOST || "data-proxy.local";
const POSTGREST_URL =
    __ENV.POSTGREST_URL ||
    "http://data-proxy-postgrest.data-proxy.svc.cluster.local:3000";
const K6_PROFILE = __ENV.K6_PROFILE || "smoke";
const HA_MODE = (__ENV.HA_MODE || "false") === "true";
const TOKEN_REFRESH_SECONDS = 30;
const BIGQUERY_LIMIT_MAX = 20;
const HEAVY_LIMIT_MIN = 1000;
const PARTITION_COLUMN = "date";
const BOTTLENECK_SHARE = Number(__ENV.K6_BOTTLENECK_SHARE || "0.05");
const BIGQUERY_SHARE = Number(__ENV.K6_BIGQUERY_SHARE || "0.05");
const PEAK_RATE = Number(__ENV.K6_PEAK_RATE || "100");

const CLIENT_IDS = [
    OIDC_USER_CLIENT_ID,
    OIDC_NO_POLICY_CLIENT_ID,
    OIDC_USER_CLIENT_ID,
];
const LOAD_TABLES = ["full_table", "multi_rls_table", "partitioned_table", "big_table"];

const PEAK_ITERATIONS = Math.round(PEAK_RATE / (1 + BIGQUERY_SHARE));

function iterationsForPeak(fraction: number): number {
    return Math.max(1, Math.round(PEAK_ITERATIONS * fraction));
}

const PROFILES: Record<string, Profile> = {
    smoke: {
        executor: "ramping-vus",
        stages: [
            { target: 1, duration: "10s" },
            { target: 1, duration: "20s" },
            { target: 0, duration: "10s" },
        ],
    },
    load: {
        executor: "ramping-arrival-rate",
        startRate: 0,
        timeUnit: "1s",
        preAllocatedVUs: PEAK_ITERATIONS,
        maxVUs: PEAK_ITERATIONS * 3,
        duration: "33m",
        stages: [
            { target: iterationsForPeak(0.5), duration: "120s" },
            { target: iterationsForPeak(1), duration: "300s" },
            { target: iterationsForPeak(1), duration: "25m" },
            { target: 0, duration: "60s" },
        ],
    },
    stress: {
        executor: "ramping-arrival-rate",
        startRate: 0,
        timeUnit: "1s",
        preAllocatedVUs: PEAK_ITERATIONS,
        maxVUs: PEAK_ITERATIONS * 6,
        duration: "570s",
        stages: [
            { target: iterationsForPeak(0.5), duration: "180s" },
            { target: iterationsForPeak(1), duration: "180s" },
            { target: iterationsForPeak(1.5), duration: "180s" },
            { target: iterationsForPeak(2), duration: "180s" },
            { target: 0, duration: "30s" },
        ],
    },
};

if (!(K6_PROFILE in PROFILES)) {
    throw new Error(`Unknown K6_PROFILE: ${K6_PROFILE}`);
}

const profile = PROFILES[K6_PROFILE];

const loadRequestFailed = new Rate("load_request_failed");

const sourceDuration = {
    cache: new Trend("cache_duration_ms"),
    ducklake: new Trend("ducklake_duration_ms"),
    bigquery: new Trend("bigquery_duration_ms"),
};
const ducklakeHeavyDuration = new Trend("ducklake_heavy_duration_ms");
const ducklakeSelectiveDuration = new Trend("ducklake_selective_duration_ms");
const ducklakePinnedDuration = new Trend("ducklake_pinned_duration_ms");

const stackThresholds: Record<string, string[]> = {
    checks: ["rate==1"],
    load_request_failed: ["rate<0.01"],
    http_req_failed: ["rate<0.01"],
    cache_duration_ms: ["p(95)<50", "p(99)<100"],
    ducklake_duration_ms: ["p(95)<300", "p(99)<600"],
};

const clientRiskThresholds: Record<string, string[]> = {
    bigquery_duration_ms: ["p(95)<6000"],
    ducklake_heavy_duration_ms: ["p(95)<2000", "p(99)<5000"],
    ducklake_selective_duration_ms: ["p(95)<2000", "p(99)<5000"],
    ducklake_pinned_duration_ms: ["p(95)<2000", "p(99)<5000"],
};

const gateClientRisk = (__ENV.K6_GATE_CLIENT_RISK || "false") === "true";
const sourceThresholds: Record<string, string[]> = gateClientRisk
    ? { ...stackThresholds, ...clientRiskThresholds }
    : { ...stackThresholds };

const scalingEvents = new Counter("autoscaling_events");

if (K6_PROFILE === "stress") {
    sourceThresholds.checks = ["rate>0.95"];
    sourceThresholds.load_request_failed = ["rate<0.05"];
    sourceThresholds.http_req_failed = ["rate<0.05"];
}

const runsScalingObserver = profile.executor !== "ramping-vus" && HA_MODE;

const isArrivalRate = profile.executor !== "ramping-vus";
const gatesAutoscaling = K6_PROFILE === "stress";

if (isArrivalRate) {
    sourceThresholds.dropped_iterations = ["count==0"];
}
if (runsScalingObserver && gatesAutoscaling) {
    sourceThresholds.autoscaling_events = ["count>0"];
}

function buildScenarios(): Record<string, unknown> {
    const built: Record<string, unknown> = {
        default: {
            executor: profile.executor,
            stages: profile.stages ?? [],
            ...(profile.executor === "ramping-arrival-rate"
                ? {
                    startRate: profile.startRate,
                    timeUnit: profile.timeUnit,
                    preAllocatedVUs: profile.preAllocatedVUs,
                    maxVUs: profile.maxVUs,
                }
                : {}),
        },
    };

    if (runsScalingObserver) {
        built.scaling_observer = {
            executor: "constant-vus",
            vus: 1,
            duration: profile.duration ?? "10m",
            exec: "observeScaling",
        };
    }

    return built;
}

export const options = {
    setupTimeout: "20m",
    scenarios: buildScenarios(),
    thresholds: sourceThresholds,
};

function authorizedRows(
    field: string,
    allowed: string[],
): (body: unknown) => boolean {
    return (body: unknown): boolean => {
        if (!Array.isArray(body) || body.length === 0) return false;
        return body.every(
            (row) =>
                typeof row === "object" &&
                row !== null &&
                allowed.includes(String((row as Record<string, unknown>)[field])),
        );
    };
}

function groupedCounts(field: string, allowed: string[]): (body: unknown) => boolean {
    return (body: unknown): boolean => {
        if (!Array.isArray(body) || body.length === 0) return false;
        return body.every((row) => {
            const item = row as Record<string, unknown>;
            return (
                allowed.includes(String(item[field])) &&
                typeof item.count === "number" &&
                item.count > 0
            );
        });
    };
}

function sortedDescending(field: string): (body: unknown) => boolean {
    return (body: unknown): boolean => {
        if (!Array.isArray(body)) return false;
        const values = body.map((row) => String((row as Record<string, unknown>)[field]));
        return values.every((value, index) => index === 0 || values[index - 1] >= value);
    };
}

function hasStatus(status: string): (body: unknown) => boolean {
    return (body: unknown): boolean => {
        if (!Array.isArray(body) || body.length === 0) return false;
        return body.every((row) => {
            const metadata = (row as { metadata?: { status?: string } }).metadata;
            return metadata?.status === status;
        });
    };
}

function allOf(...checks: Array<(body: unknown) => boolean>): (body: unknown) => boolean {
    return (body: unknown): boolean => checks.every((item) => item(body));
}

const ACCESS_POLICY_ROWS = [
    { subject: "test_user_1", unit_type: "unit", unit_id: "unit_1" },
    { subject: "test_user_1", unit_type: "region", unit_id: "region_1" },
    { subject: "test_user_1", unit_type: "group", unit_id: "group_1" },
];

const ROUTES: Route[] = [
    {
        profile: "test",
        path: "/full_table?unit_id=eq.unit_1&limit=20",
        name: "test_full_table_unit_1",
        expectedSource: "ducklake",
        weight: 30,
        clients: ["user"],
        checkBody: authorizedRows("unit_id", ["unit_1"]),
    },
    {
        profile: "test",
        path: "/full_table?unit_id=eq.unit_2&limit=20",
        name: "test_full_table_unit_2",
        expectedSource: "ducklake",
        weight: 30,
        clients: ["no_policy"],
        checkBody: authorizedRows("unit_id", ["unit_2"]),
    },
    {
        profile: "test",
        path: "/multi_rls_table?region_id=eq.region_1&limit=20",
        name: "test_multi_rls_table_region_1",
        expectedSource: "ducklake",
        weight: 20,
        clients: ["user"],
        checkBody: authorizedRows("region_id", ["region_1"]),
    },
    {
        profile: "test",
        path: "/multi_rls_table?group_id=eq.group_1&limit=20",
        name: "test_multi_rls_table_group_1",
        expectedSource: "ducklake",
        weight: 15,
        clients: ["user"],
        checkBody: authorizedRows("group_id", ["group_1"]),
    },
    {
        profile: "test",
        path: "/partitioned_table?unit_id=eq.unit_1&limit=20&order=date.desc",
        name: "test_partitioned_table_unit_1",
        expectedSource: "ducklake+bigquery",
        weight: 15,
        clients: ["user"],
        checkBody: authorizedRows("unit_id", ["unit_1"]),
    },
    {
        profile: "test",
        path: "/multi_rls_table?select=region_id,group_id,count()",
        name: "test_multi_rls_table_group_by",
        expectedSource: "ducklake",
        heavy: true,
        weight: 5,
        clients: ["user"],
        checkBody: groupedCounts("region_id", ["region_1", "region_2", "region_3"]),
    },
    {
        profile: "test",
        path: "/big_table?select=metadata->>status,count()",
        name: "test_big_table_group_by_json",
        expectedSource: "ducklake",
        heavy: true,
        weight: 5,
        clients: ["user"],
        checkBody: groupedCounts("status", ["active", "inactive", "pending"]),
    },
    {
        profile: "test",
        path: "/big_table?select=id,name,unit_id,metadata&order=name.desc",
        name: "test_big_table_scan_sorted",
        expectedSource: "ducklake",
        heavy: true,
        weight: 5,
        clients: ["user"],
        checkBody: allOf(authorizedRows("unit_id", ["unit_1"]), sortedDescending("name")),
    },
    {
        profile: "test",
        path: "/big_table?select=id,unit_id,metadata&metadata->>status=eq.active&order=id.desc",
        name: "test_big_table_json_filter_sorted",
        expectedSource: "ducklake",
        heavy: true,
        weight: 5,
        clients: ["user"],
        checkBody: allOf(
            authorizedRows("unit_id", ["unit_1"]),
            hasStatus("active"),
            sortedDescending("id"),
        ),
    },
];

/** Fetches an OIDC access token with its expiry time. */
function fetchToken(clientId: string): TokenData {
    const response = http.post(OIDC_TOKEN_URL, {
        grant_type: "client_credentials",
        client_id: clientId,
        client_secret: OIDC_CLIENT_SECRET,
    }) as K6Response;
    const body = safeJson(response) as TokenResponse | null;
    check(response, {
        "token request succeeded": (item: K6Response) => item.status === 200,
    });
    if (!body?.access_token) {
        throw new Error(`Token request did not return access_token: ${response.body}`);
    }
    return {
        token: body.access_token,
        expiresAt: Date.now() + (body.expires_in || 300) * 1000,
    };
}

/** Seeds the access policy table so RLS grants the test users their units. */
function seedAccessPolicy(rows: typeof ACCESS_POLICY_ROWS): void {
    const token = fetchToken(OIDC_POLICY_WRITER_CLIENT_ID);
    const headers = {
        Authorization: `Bearer ${token.token}`,
        Host: HOST,
        "Accept-Profile": "test",
        "Content-Type": "application/json",
        Prefer: "resolution=merge-duplicates",
    };
    const response = http.post(
        `${API_URL}/access_policy`,
        JSON.stringify(rows),
        {
            headers,
            tags: { name: "seed_access_policy" },
            // A policy that already exists answers 409, which is not a failure.
            responseCallback: http.expectedStatuses(201, 409),
        },
    ) as K6Response;
    check(response, {
        "access_policy seeded": (item: K6Response) =>
            item.status === 201 || item.status === 409,
    });
}

const SECOND_ACCESS_POLICY_ROWS = [
    { subject: "test_user_2", unit_type: "unit", unit_id: "unit_2" },
];

const SETUP_ATTEMPTS = 6;
const SETUP_RETRY_SECONDS = 5;

/** Reports whether an answer is not JSON, which is what a proxy returns while a pod restarts. */
function isTransient(response: K6Response): boolean {
    if (response.status === 0) return true;
    try {
        JSON.parse(String(response.body));
        return false;
    } catch {
        return true;
    }
}

/** Sends a setup request again while the answer is a transient proxy error, such as during a PostgREST restart. */
function setupGet(url: string, params: RequestParams): K6Response {
    let response = http.get(url, params) as K6Response;
    for (let attempt = 1; attempt < SETUP_ATTEMPTS && isTransient(response); attempt++) {
        sleep(SETUP_RETRY_SECONDS);
        response = http.get(url, params) as K6Response;
    }
    return response;
}

/** Verifies that a user without a policy gets a 200 with zero rows. */
function verifyNoAccess(): void {
    const token = fetchToken(OIDC_NO_POLICY_CLIENT_ID);
    const response = setupGet(`${API_URL}/full_table?limit=1`, {
        headers: {
            Authorization: `Bearer ${token.token}`,
            Host: HOST,
            "Accept-Profile": "test",
        },
        tags: { name: "no_access_check" },
    }) as K6Response;
    const body = safeJson(response);
    check(response, {
        "user without policy gets 200": (item: K6Response) => item.status === 200,
        "no-access returns zero rows": () => Array.isArray(body) && body.length === 0,
        "no-access queries no source": () => header(response, "X-Source") === "",
        "no-access response omits snapshot": () =>
            header(response, "X-DuckLake-Snapshot") === "",
    });
}

/** Waits until the sync restores every local load-test table. */
function waitForLocalTables(token: string): void {
    const deadline = Date.now() + 1_200_000;
    let missing = LOAD_TABLES;

    while (Date.now() < deadline) {
        missing = LOAD_TABLES.filter((table) => {
            const response = setupGet(`${POSTGREST_URL}/${table}?limit=1`, {
                headers: {
                    Authorization: `Bearer ${token}`,
                    "Accept-Profile": "test",
                },
                tags: { name: `load_setup:${table}` },
            }) as K6Response;
            const body = safeJson(response);
            return response.status !== 200 || !Array.isArray(body) || body.length === 0;
        });
        if (missing.length === 0) return;
        sleep(2);
    }

    throw new Error(`Local tables were not populated before load: ${missing.join(", ")}`);
}

/** Returns the two days immediately before the oldest DuckLake partition, read from a pinned snapshot. */
function bigqueryDates(token: string): string[] {
    const headers = { Authorization: `Bearer ${token}`, "Accept-Profile": "test" };
    const snapshot = setupGet(`${POSTGREST_URL}/rpc/ducklake_latest_snapshot`, {
        headers,
        tags: { name: "load_setup:snapshot" },
    }) as K6Response;
    if (snapshot.status !== 200) {
        throw new Error(`Could not read the latest DuckLake snapshot: ${snapshot.body}`);
    }
    const response = setupGet(
        `${POSTGREST_URL}/partitioned_table?select=${PARTITION_COLUMN}&order=${PARTITION_COLUMN}.asc&limit=1`,
        {
            headers: { ...headers, "X-DuckLake-Snapshot": String(safeJson(snapshot) ?? "") },
            tags: { name: "load_setup:bigquery_dates" },
        },
    ) as K6Response;
    const body = safeJson(response);
    const rows = Array.isArray(body) ? (body as Record<string, unknown>[]) : [];
    const partition = String(rows[0]?.[PARTITION_COLUMN] || "");
    if (response.status !== 200 || !/^\d{4}-\d{2}-\d{2}$/.test(partition)) {
        throw new Error(`Could not discover the oldest DuckLake partition: ${response.body}`);
    }

    const date = new Date(`${partition}T00:00:00Z`);
    const dates: string[] = [];
    for (let days = 1; days <= 2; days++) {
        const older = new Date(date.getTime());
        older.setUTCDate(older.getUTCDate() - days);
        dates.push(older.toISOString().slice(0, 10));
    }
    return dates;
}

/** Returns the first ids that the test user can read from big_table. */
function visibleIds(token: string): string[] {
    const response = setupGet(`${POSTGREST_URL}/big_table?select=id&limit=1000`, {
        headers: { Authorization: `Bearer ${token}`, "Accept-Profile": "test" },
        tags: { name: "load_setup:ids" },
    }) as K6Response;
    const body = safeJson(response);
    const ids = Array.isArray(body)
        ? body.map((row) => String((row as Record<string, unknown>).id))
        : [];
    if (response.status !== 200 || ids.length === 0) {
        throw new Error(`Could not read any big_table id: ${response.body}`);
    }
    return ids;
}

/** Returns the latest snapshot and the older snapshots that big_table can still be read at. */
function readableSnapshots(token: string): string[] {
    const headers = { Authorization: `Bearer ${token}`, "Accept-Profile": "test" };
    const latest = setupGet(`${POSTGREST_URL}/rpc/ducklake_latest_snapshot`, {
        headers,
        tags: { name: "load_setup:snapshot" },
    }) as K6Response;
    const parsed = safeJson(latest);
    const current = Number(parsed);
    if (latest.status !== 200 || parsed === null || !Number.isInteger(current)) {
        throw new Error(`Could not read the latest DuckLake snapshot: ${latest.body}`);
    }
    return [0, 1, 5, 20, 50]
        .map((age) => String(current - age))
        .filter((snapshot) => {
            const response = setupGet(`${POSTGREST_URL}/big_table?select=id&limit=1`, {
                headers: { ...headers, "X-DuckLake-Snapshot": snapshot },
                tags: { name: "load_setup:snapshot_probe" },
            }) as K6Response;
            const body = safeJson(response);
            return response.status === 200 && Array.isArray(body) && body.length > 0;
        });
}

/** Fetches tokens for every test user before the load test starts. */
export function setup(): SetupData {
    const k8s = new Kubernetes();
    seedAccessPolicy(ACCESS_POLICY_ROWS);
    const tokens = CLIENT_IDS.map((id) => fetchToken(id));
    waitForLocalTables(tokens[0].token);
    verifyNoAccess();
    seedAccessPolicy(SECOND_ACCESS_POLICY_ROWS);
    return {
        tokens,
        bigqueryDates: bigqueryDates(tokens[0].token),
        ids: visibleIds(tokens[0].token),
        snapshots: readableSnapshots(tokens[0].token),
        replicaBaseline: scalingReplicaCounts(k8s),
    };
}

const vuTokens: Record<string, TokenData> = {};

/** Returns a valid token for the given client, refreshing it before it expires. */
function ensureToken(clientId: string, current: TokenData): TokenData {
    const cached = vuTokens[clientId];
    if (cached && Date.now() < cached.expiresAt - TOKEN_REFRESH_SECONDS * 1000) {
        return cached;
    }
    const next = cached ? fetchToken(clientId) : current;
    vuTokens[clientId] = next;
    return next;
}

/** Picks a weighted route that the selected client can read. */
function pickRoute(clientId: string): Route {
    const candidates = ROUTES.filter((route) =>
        !route.heavy && route.clients.indexOf(clientId) !== -1,
    );
    const totalWeight = candidates.reduce((sum, route) => sum + route.weight, 0);
    let value = Math.random() * totalWeight;

    for (const route of candidates) {
        value -= route.weight;
        if (value < 0) return route;
    }

    throw new Error(`No load route is configured for client: ${clientId}`);
}

/**
Parses a JSON body, or returns null when the response carries no JSON, such as a
gateway error page. An unparseable body must count as a failure, not abort the iteration.
*/
function safeJson(response: K6Response): unknown {
    if (response.status === 0 || !response.body) return null;
    try {
        return response.json();
    } catch {
        return null;
    }
}

/** Returns the header value of a response, whatever its case. */
function header(response: K6Response, name: string): string {
    const wanted = name.toLowerCase();
    const key = Object.keys(response.headers).find(
        (candidate) => candidate.toLowerCase() === wanted,
    );
    return key ? response.headers[key] : "";
}

/** Classifies a response as a cache hit, or by the sources that served it. */
function sourceOf(
    response: K6Response,
): "cache" | "ducklake" | "bigquery" | null {
    if (header(response, "X-Cache") === "HIT") return "cache";
    const source = header(response, "X-Source");
    if (source === "ducklake") return "ducklake";
    if (source === "bigquery" || source === "ducklake+bigquery") {
        return "bigquery";
    }
    return null;
}

/** Records the response latency under its source. */
function recordSourceDuration(response: K6Response, ducklakeTrend?: Trend): void {
    const source = sourceOf(response);
    if (source === "ducklake" && ducklakeTrend) {
        ducklakeTrend.add(response.timings.duration);
    } else if (source !== null) {
        sourceDuration[source].add(response.timings.duration);
    }
}

/** Sends one request to a route and checks the status and body shape. */
function request(route: Route, token: string): void {
    const path = route.heavy
        ? `${route.path}&limit=${HEAVY_LIMIT_MIN + Math.floor(Math.random() * HEAVY_LIMIT_MIN)}`
        : route.path;
    const response = http.get(`${API_URL}${path}`, {
        headers: {
            Authorization: `Bearer ${token}`,
            Host: HOST,
            "Accept-Profile": route.profile,
        },
        tags: { name: route.name, tag: route.name },
    }) as K6Response;
    recordSourceDuration(response, route.heavy ? ducklakeHeavyDuration : undefined);
    loadRequestFailed.add(response.status !== 200);
    const body = safeJson(response);
    check(response, {
        [`${route.name} returned 200`]: (item: K6Response) => item.status === 200,
        [`${route.name} returned JSON array`]: () => route.checkBody(body),
        [`${route.name} reports ${route.expectedSource}`]: () =>
            header(response, "X-Source") === route.expectedSource,
    });
}

/** Requests an older partition from BigQuery and verifies the repeated request is cached. */
function requestBigQueryPair(clientId: string, token: string, dates: string[]): void {
    const date = dates[Math.floor(Math.random() * dates.length)];
    const limit = Math.floor(Math.random() * BIGQUERY_LIMIT_MAX) + 1;
    const path =
        `/partitioned_table?date=eq.${date}&select=id,date,unit_id&limit=${limit}`;
    const params = {
        headers: {
            Authorization: `Bearer ${token}`,
            Host: HOST,
            "Accept-Profile": "test",
        },
        tags: { name: `bigquery:${clientId}` },
    };
    const first = http.get(`${API_URL}${path}`, params) as K6Response;
    recordSourceDuration(first);
    loadRequestFailed.add(first.status !== 200);
    const firstBody = safeJson(first);
    const second = http.get(`${API_URL}${path}`, params) as K6Response;
    recordSourceDuration(second);
    loadRequestFailed.add(second.status !== 200);
    check(first, {
        "BigQuery request returned 200": (item: K6Response) => item.status === 200,
        "BigQuery request reaches BigQuery or a warm cache hit": (
            item: K6Response,
        ) => {
            const source = sourceOf(item);
            return source === "bigquery" || source === "cache";
        },
        "BigQuery rows belong to the selected partition": () =>
            authorizedRows(PARTITION_COLUMN, [date])(firstBody),
        "BigQuery rows obey RLS": () =>
            authorizedRows("unit_id", ["unit_1"])(firstBody),
    });
    check(second, {
        "BigQuery repeat returned 200": (item: K6Response) => item.status === 200,
        "BigQuery repeat is cached": (item: K6Response) =>
            sourceOf(item) === "cache",
        "BigQuery repeat returns the same rows": () => second.body === first.body,
    });
}

/** Picks a route and a user, sends one request, then sleeps a random duration. */
export default function(data: SetupData): void {
    const vu = (__VU - 1) % CLIENT_IDS.length;
    const clientId = CLIENT_IDS[vu];
    const token = ensureToken(clientId, data.tokens[vu]);
    const pick = Math.random();
    if (pick < BOTTLENECK_SHARE) {
        bottleneckCase(token.token, data);
    } else if (pick < BOTTLENECK_SHARE + BIGQUERY_SHARE) {
        requestBigQueryPair(clientId, token.token, data.bigqueryDates);
    } else {
        request(pickRoute(clientId), token.token);
    }
    if (profile.executor === "ramping-vus") sleep(Math.random() * 3 + 0.5);
}

/** Polls replica counts throughout stress, and records when autoscaling increases a workload. */
export function observeScaling(data: SetupData): void {
    const k8s = new Kubernetes();
    const names = Object.keys(data.replicaBaseline);
    const observed: Record<string, boolean> = {};
    const deadline = Date.now() + durationSeconds(profile.duration || "0s") * 1000;

    while (Date.now() < deadline) {
        for (const name of names) {
            try {
                const deployment = k8s.get("Deployment.apps", name, NAMESPACE) as ScaleDeployment;
                const replicas = deployment.spec.replicas || 0;
                if (
                    replicas > data.replicaBaseline[name] &&
                    (deployment.status?.availableReplicas || 0) >= replicas &&
                    !observed[name]
                ) {
                    observed[name] = true;
                    scalingEvents.add(1, { deployment: name });
                    console.log(`Autoscaling observed: ${name} ${data.replicaBaseline[name]} -> ${replicas}`);
                }
            } catch {
                continue;
            }
        }
        sleep(5);
    }
}

function durationSeconds(value: string): number {
    const match = /^(\d+)(s|m|h)$/.exec(value);
    if (!match) throw new Error(`Unsupported profile duration: ${value}`);
    const scale = { s: 1, m: 60, h: 3600 }[match[2] as "s" | "m" | "h"];
    return Number(match[1]) * scale;
}

function scalingReplicaCounts(k8s: Kubernetes): Record<string, number> {
    const deployments = k8s.list("Deployment.apps", NAMESPACE) as ScaleDeployment[];
    const baseline: Record<string, number> = {};
    for (const deployment of deployments) {
        const component = deployment.metadata.labels?.["app.kubernetes.io/component"];
        if (component === "proxy" || component === "postgrest" || component === "postgrest-ro") {
            baseline[deployment.metadata.name] = deployment.spec.replicas || 0;
        }
    }
    return baseline;
}

function randomItem<T>(items: T[]): T {
    return items[Math.floor(Math.random() * items.length)];
}

function uniqueLimit(): number {
    return HEAVY_LIMIT_MIN + Math.floor(Math.random() * HEAVY_LIMIT_MIN);
}

/**
 * Looks up one row by id. Postgres filters the rows only after DuckLake returns
 * every visible row, so this latency grows with the table size and not with the answer.
 */
function selectiveLookup(token: string, ids: string[]): void {
    const id = randomItem(ids);
    const response = http.get(
        `${API_URL}/big_table?select=id,unit_id&id=eq.${id}&limit=${uniqueLimit()}`,
        {
            headers: { Authorization: `Bearer ${token}`, Host: HOST, "Accept-Profile": "test" },
            tags: { name: "selective_lookup" },
        },
    ) as K6Response;
    recordSourceDuration(response, ducklakeSelectiveDuration);
    loadRequestFailed.add(response.status !== 200);
    const body = safeJson(response);
    check(response, {
        "selective lookup returned 200": (item: K6Response) => item.status === 200,
        "selective lookup returned the requested row": () =>
            Array.isArray(body) &&
            body.length === 1 &&
            String((body[0] as Record<string, unknown>).id) === id &&
            (body[0] as Record<string, unknown>).unit_id === "unit_1",
        "selective lookup reads DuckLake": () => header(response, "X-Source") === "ducklake",
    });
}

/** Reads big_table at the latest and at older pinned snapshots. */
function pinnedSnapshot(token: string, snapshots: string[]): void {
    const snapshot = randomItem(snapshots);
    const response = http.get(
        `${API_URL}/big_table?unit_id=eq.unit_1&limit=${uniqueLimit()}`,
        {
            headers: {
                Authorization: `Bearer ${token}`,
                Host: HOST,
                "Accept-Profile": "test",
                "X-DuckLake-Snapshot": snapshot,
            },
            tags: { name: "pinned_snapshot" },
        },
    ) as K6Response;
    recordSourceDuration(response, ducklakePinnedDuration);
    loadRequestFailed.add(response.status !== 200);
    const body = safeJson(response);
    check(response, {
        "pinned snapshot returned 200": (item: K6Response) => item.status === 200,
        "pinned snapshot returned authorized rows": () =>
            authorizedRows("unit_id", ["unit_1"])(body),
        "pinned snapshot reads DuckLake only": () => header(response, "X-Source") === "ducklake",
        "pinned snapshot reports the requested snapshot": () =>
            header(response, "X-DuckLake-Snapshot") === snapshot,
    });
}

/** Sends one bottleneck case: half heavy queries, a quarter each of lookups and pinned reads. */
function bottleneckCase(token: string, data: SetupData): void {
    const pick = Math.random();
    if (pick < 0.5) {
        request(randomItem(ROUTES.filter((route) => route.heavy)), token);
    } else if (pick < 0.75) {
        selectiveLookup(token, data.ids);
    } else {
        pinnedSnapshot(token, data.snapshots);
    }
}
