import http from "k6/http";
import type { Response as K6Response } from "k6/http";
import { check, sleep } from "k6";
import { Kubernetes } from "k6/x/kubernetes";
import { Rate, Trend } from "k6/metrics";
import { triggerSync, waitForJob } from "./lib.ts";

declare const __ENV: Record<string, string | undefined>;

type TokenData = { token: string; expiresAt: number };
type SetupData = { tokens: TokenData[]; fallbackDates: string[] };
type TokenResponse = { access_token?: string; expires_in?: number };
type Route = {
    profile: string;
    path: string;
    name: string;
    expectedSource: "ducklake" | "ducklake+bigquery";
    weight: number;
    clients: string[];
    checkBody: (body: unknown) => boolean;
};
type Stage = { target: number; duration: string };
type Profile = { stages: Stage[] };

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
const TOKEN_REFRESH_SECONDS = 30;
const FALLBACK_LIMIT_MAX = 20;
const PARTITION_COLUMN = "date";

const CLIENT_IDS = [
    OIDC_USER_CLIENT_ID,
    OIDC_USER_CLIENT_ID,
    OIDC_USER_CLIENT_ID,
];
const LOAD_TABLES = ["full_table", "multi_rls_table", "partitioned_table"];

const PROFILES: Record<string, Profile> = {
    smoke: {
        stages: [
            { target: 1, duration: "10s" },
            { target: 1, duration: "20s" },
            { target: 0, duration: "10s" },
        ],
    },
    load: {
        stages: [
            { target: 5, duration: "30s" },
            { target: 10, duration: "4m" },
            { target: 0, duration: "30s" },
        ],
    },
    stress: {
        stages: [
            { target: 10, duration: "15s" },
            { target: 10, duration: "90s" },
            { target: 20, duration: "15s" },
            { target: 20, duration: "90s" },
            { target: 30, duration: "15s" },
            { target: 30, duration: "90s" },
            { target: 50, duration: "15s" },
            { target: 50, duration: "90s" },
            { target: 75, duration: "15s" },
            { target: 75, duration: "90s" },
            { target: 100, duration: "15s" },
            { target: 100, duration: "90s" },
            { target: 0, duration: "15s" },
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

const sourceThresholds = {
    checks: ["rate==1"],
    load_request_failed: ["rate<0.01"],
    cache_duration_ms: ["p(95)<50"],
    ducklake_duration_ms: ["p(95)<300"],
    bigquery_duration_ms: ["p(95)<4000"],
};


export const options = {
    setupTimeout: "6m",
    scenarios: {
        default: {
            executor: "ramping-vus",
            stages: profile.stages,
        },
    },
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
];

/** Fetches an OIDC access token with its expiry time. */
function fetchToken(clientId: string): TokenData {
    const response = http.post(OIDC_TOKEN_URL, {
        grant_type: "client_credentials",
        client_id: clientId,
        client_secret: OIDC_CLIENT_SECRET,
    }) as K6Response;
    const body = response.json() as TokenResponse;
    check(response, {
        "token request succeeded": (item: K6Response) => item.status === 200,
    });
    if (!body.access_token) {
        throw new Error(`Token request did not return access_token: ${response.body}`);
    }
    return {
        token: body.access_token,
        expiresAt: Date.now() + (body.expires_in || 300) * 1000,
    };
}

/** Seeds the access policy table so RLS grants the test users their units. */
function seedAccessPolicy(): void {
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
        JSON.stringify(ACCESS_POLICY_ROWS),
        { headers, tags: { name: "seed_access_policy" } },
    ) as K6Response;
    check(response, {
        "access_policy seeded": (item: K6Response) =>
            item.status === 201 || item.status === 409,
    });
}

/** Verifies that a user without a policy gets a 200 with zero rows. */
function verifyNoAccess(): void {
    const token = fetchToken(OIDC_NO_POLICY_CLIENT_ID);
    const response = http.get(`${API_URL}/full_table?limit=1`, {
        headers: {
            Authorization: `Bearer ${token.token}`,
            Host: HOST,
            "Accept-Profile": "test",
        },
        tags: { name: "no_access_check" },
    }) as K6Response;
    const body = response.json();
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
    const deadline = Date.now() + 300_000;
    let missing = LOAD_TABLES;

    while (Date.now() < deadline) {
        missing = LOAD_TABLES.filter((table) => {
            const response = http.get(`${POSTGREST_URL}/${table}?limit=1`, {
                headers: {
                    Authorization: `Bearer ${token}`,
                    "Accept-Profile": "test",
                },
                tags: { name: `load_setup:${table}` },
            }) as K6Response;
            const body = response.json();
            return response.status !== 200 || !Array.isArray(body) || body.length === 0;
        });
        if (missing.length === 0) return;
        sleep(2);
    }

    throw new Error(`Local tables were not populated before load: ${missing.join(", ")}`);
}

/** Returns the two days immediately before the oldest local protocol partition. */
function fallbackDates(token: string): string[] {
    const response = http.get(
        `${POSTGREST_URL}/partitioned_table?select=${PARTITION_COLUMN}&order=${PARTITION_COLUMN}.asc&limit=1`,
        { headers: { Authorization: `Bearer ${token}`, "Accept-Profile": "test" }, tags: { name: "load_setup:fallback_dates" } },
    ) as K6Response;
    const body = response.json();
    const rows = Array.isArray(body) ? (body as Record<string, unknown>[]) : [];
    const partition = String(rows[0]?.[PARTITION_COLUMN] || "");
    if (response.status !== 200 || !/^\d{4}-\d{2}-\d{2}$/.test(partition)) {
        throw new Error(`Could not discover oldest local protocol partition: ${response.body}`);
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

/** Fetches tokens for every test user before the load test starts. */
export function setup(): SetupData {
    const k8s = new Kubernetes();
    waitForJob(k8s, triggerSync(k8s));
    seedAccessPolicy();
    const tokens = CLIENT_IDS.map((id) => fetchToken(id));
    waitForLocalTables(tokens[0].token);
    verifyNoAccess();
    return { tokens, fallbackDates: fallbackDates(tokens[0].token) };
}

const vuTokens: Record<string, TokenData> = {};

/** Returns a valid token for the given client, refreshing it before it expires. */
function ensureToken(clientId: string, fallback: TokenData): TokenData {
    const cached = vuTokens[clientId];
    if (cached && Date.now() < cached.expiresAt - TOKEN_REFRESH_SECONDS * 1000) {
        return cached;
    }
    const next = cached ? fetchToken(clientId) : fallback;
    vuTokens[clientId] = next;
    return next;
}

/** Picks a weighted route that the selected client can read. */
function pickRoute(clientId: string): Route {
    const candidates = ROUTES.filter((route) =>
        route.clients.indexOf(clientId) !== -1,
    );
    const totalWeight = candidates.reduce((sum, route) => sum + route.weight, 0);
    let value = Math.random() * totalWeight;

    for (const route of candidates) {
        value -= route.weight;
        if (value < 0) return route;
    }

    throw new Error(`No load route is configured for client: ${clientId}`);
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
function recordSourceDuration(response: K6Response): void {
    const source = sourceOf(response);
    if (source !== null) {
        sourceDuration[source].add(response.timings.duration);
    }
}

/** Sends one request to a route and checks the status and body shape. */
function request(route: Route, token: string): void {
    const response = http.get(`${API_URL}${route.path}`, {
        headers: {
            Authorization: `Bearer ${token}`,
            Host: HOST,
            "Accept-Profile": route.profile,
        },
        tags: { name: route.name, tag: route.name },
    }) as K6Response;
    recordSourceDuration(response);
    loadRequestFailed.add(response.status !== 200);
    const body = response.json();
    check(response, {
        [`${route.name} returned 200`]: (item: K6Response) => item.status === 200,
        [`${route.name} returned JSON array`]: () => route.checkBody(body),
        [`${route.name} reports ${route.expectedSource}`]: () =>
            header(response, "X-Source") === route.expectedSource,
    });
}

/** Requests an older partition from BigQuery and verifies the repeated request is cached. */
function requestFallbackPair(clientId: string, token: string, dates: string[]): void {
    const date = dates[Math.floor(Math.random() * dates.length)];
    const limit = Math.floor(Math.random() * FALLBACK_LIMIT_MAX) + 1;
    const path =
        `/partitioned_table?date=eq.${date}&select=id,date,unit_id&limit=${limit}`;
    const params = {
        headers: {
            Authorization: `Bearer ${token}`,
            Host: HOST,
            "Accept-Profile": "test",
        },
        tags: { name: `bigquery_protocol:${clientId}` },
    };
    const first = http.get(`${API_URL}${path}`, params) as K6Response;
    recordSourceDuration(first);
    loadRequestFailed.add(first.status !== 200);
    const firstBody = first.json();
    const second = http.get(`${API_URL}${path}`, params) as K6Response;
    recordSourceDuration(second);
    loadRequestFailed.add(second.status !== 200);
    const secondBody = second.json();
    check(first, {
        "BigQuery protocol request returned 200": (item: K6Response) => item.status === 200,
        "BigQuery protocol request reaches BigQuery or a warm cache hit": (
            item: K6Response,
        ) => {
            const source = sourceOf(item);
            return source === "bigquery" || source === "cache";
        },
        "BigQuery protocol rows belong to the selected partition": () =>
            authorizedRows(PARTITION_COLUMN, [date])(firstBody),
        "BigQuery protocol rows obey RLS": () =>
            authorizedRows("unit_id", ["unit_1"])(firstBody),
    });
    check(second, {
        "BigQuery protocol repeat returned 200": (item: K6Response) => item.status === 200,
        "BigQuery protocol repeat is cached": (item: K6Response) =>
            sourceOf(item) === "cache",
        "BigQuery protocol repeat returns the same rows": () =>
            JSON.stringify(secondBody) === JSON.stringify(firstBody),
    });
}

/** Picks a route and a user, sends one request, then sleeps a random duration. */
export default function(data: SetupData): void {
    const vu = (__VU - 1) % CLIENT_IDS.length;
    const clientId = CLIENT_IDS[vu];
    const token = ensureToken(clientId, data.tokens[vu]);
    if (Math.random() < 0.25) {
        requestFallbackPair(clientId, token.token, data.fallbackDates);
    } else {
        request(pickRoute(clientId), token.token);
    }
    sleep(Math.random() * 3 + 0.5);
}
