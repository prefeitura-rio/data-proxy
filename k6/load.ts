import http from "k6/http";
import { check, sleep } from "k6";
import { Kubernetes } from "k6/x/kubernetes";
import { Rate, Trend } from "k6/metrics";
import { triggerSync, waitForJob } from "./lib.ts";

declare const __ENV: Record<string, string | undefined>;

type TokenData = { token: string; expiresAt: number };
type SetupData = { tokens: TokenData[]; fallbackDates: string[] };
type TokenResponse = { access_token?: string; expires_in?: number };
type K6Response = {
    status: number;
    body: string;
    headers: Record<string, string>;
    timings: { duration: number };
    json: (path?: string) => unknown;
};
type Route = {
    profile: string;
    path: string;
    name: string;
    weight: number;
    clients: string[];
    checkBody: (body: unknown) => boolean;
};
type Stage = { target: number; duration: string };
type Profile = { stages: Stage[] };

const API_URL = __ENV.BASE_URL || "http://istio-ingressgateway.istio-ingress.svc.cluster.local";
const OIDC_TOKEN_URL = __ENV.OIDC_TOKEN_URL || "http://oidc.data-proxy.svc.cluster.local:8080/token";
const OIDC_CLIENT_SECRET = __ENV.OIDC_CLIENT_SECRET || "test-secret";
const HOST = __ENV.API_HOST || "data-proxy.local";
const POSTGREST_URL = __ENV.POSTGREST_URL || "http://data-proxy-postgrest.data-proxy.svc.cluster.local:3000";
const K6_PROFILE = __ENV.K6_PROFILE || "smoke";
const TOKEN_REFRESH_SECONDS = 30;
const FALLBACK_OFFSET_MAX = 20;

const CLIENT_IDS = ["user-with-access", "user-cras-2", "user-escola-3"];
const LOAD_TABLES = ["endpoint_participante_listagem", "endpoint_participantes", "protocolo_estado_diario"];

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
    postgrest: new Trend("postgrest_duration_ms"),
    bigquery: new Trend("bigquery_duration_ms"),
};

const sourceThresholds = {
    load_request_failed: ["rate<0.01"],
    cache_duration_ms: ["p(95)<50"],
    postgrest_duration_ms: ["p(95)<300"],
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

const ACCESS_POLICY_ROWS = [{ subject: "user-1", unit_type: "unidade", unit_id: "cras_1" },
{ subject: "user-1", unit_type: "cras", unit_id: "cras_1" },
{ subject: "user-1", unit_type: "escola", unit_id: "escola_1" },
{ subject: "user-cras-2", unit_type: "unidade", unit_id: "cras_2" },
{ subject: "user-cras-2", unit_type: "cras", unit_id: "cras_2" },
{ subject: "user-escola-3", unit_type: "escola", unit_id: "escola_3" },
];

const ROUTES: Route[] = [
    {
        profile: "pic",
        path: "/endpoint_participante_listagem?id_unidade=eq.cras_1&limit=20",
        name: "pic_endpoint_participante_listagem_cras_1",
        weight: 30,
        clients: ["user-with-access"],
        checkBody: (body: unknown): boolean => Array.isArray(body),
    },
    {
        profile: "pic",
        path: "/endpoint_participante_listagem?id_unidade=eq.cras_2&limit=20",
        name: "pic_endpoint_participante_listagem_cras_2",
        weight: 30,
        clients: ["user-cras-2"],
        checkBody: (body: unknown): boolean => Array.isArray(body),
    },
    {
        profile: "pic",
        path: "/freshness?table=eq.endpoint_participante_listagem",
        name: "pic_freshness",
        weight: 10,
        clients: CLIENT_IDS,
        checkBody: (body: unknown): boolean => Array.isArray(body),
    },
    {
        profile: "pic",
        path: "/endpoint_participantes?id_cras=eq.cras_1&limit=20",
        name: "projeto_endpoint_participantes_cras_1",
        weight: 20,
        clients: ["user-with-access"],
        checkBody: (body: unknown): boolean => Array.isArray(body),
    },
    {
        profile: "pic",
        path: "/endpoint_participantes?id_cras=eq.cras_2&limit=20",
        name: "projeto_endpoint_participantes_cras_2",
        weight: 20,
        clients: ["user-cras-2"],
        checkBody: (body: unknown): boolean => Array.isArray(body),
    },
    {
        profile: "pic",
        path: "/endpoint_participantes?id_escola=eq.escola_1&limit=20",
        name: "projeto_endpoint_participantes_escola_1",
        weight: 15,
        clients: ["user-with-access"],
        checkBody: (body: unknown): boolean => Array.isArray(body),
    },
    {
        profile: "pic",
        path: "/endpoint_participantes?id_escola=eq.escola_3&limit=20",
        name: "projeto_endpoint_participantes_escola_3",
        weight: 30,
        clients: ["user-escola-3"],
        checkBody: (body: unknown): boolean => Array.isArray(body),
    },
    {
        profile: "pic",
        path: "/protocolo_estado_diario?id_unidade=eq.cras_1&limit=20&order=protocolo_data_referencia_particicao.desc",
        name: "projeto_protocolo_estado_diario_cras_1",
        weight: 15,
        clients: ["user-with-access"],
        checkBody: (body: unknown): boolean => Array.isArray(body),
    },
    {
        profile: "pic",
        path: "/protocolo_estado_diario?id_unidade=eq.cras_2&limit=20&order=protocolo_data_referencia_particicao.desc",
        name: "projeto_protocolo_estado_diario_cras_2",
        weight: 15,
        clients: ["user-cras-2"],
        checkBody: (body: unknown): boolean => Array.isArray(body),
    },
    {
        profile: "pic",
        path: "/freshness?table=eq.protocolo_estado_diario",
        name: "projeto_freshness",
        weight: 10,
        clients: CLIENT_IDS,
        checkBody: (body: unknown): boolean => Array.isArray(body),
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
    check(response, { "token request succeeded": (item: K6Response) => item.status === 200 });
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
    const token = fetchToken("policy-writer");
    const headers = {
        Authorization: `Bearer ${token.token}`,
        Host: HOST,
        "Accept-Profile": "pic",
        "Content-Type": "application/json",
        Prefer: "resolution=merge-duplicates",
    };
    const response = http.post(
        `${API_URL}/access_policy`,
        JSON.stringify(ACCESS_POLICY_ROWS),
        { headers, tags: { name: "seed_access_policy" } },
    ) as K6Response;
    check(response, {
        "access_policy seeded": (item: K6Response) => item.status === 201 || item.status === 409,
    });
}

/** Verifies that a user without a policy gets a 200 with zero rows. */
function verifyNoAccess(): void {
    const token = fetchToken("user-no-access");
    const response = http.get(
        `${API_URL}/endpoint_participante_listagem?limit=1`,
        { headers: { Authorization: `Bearer ${token.token}`, Host: HOST, "Accept-Profile": "pic" }, tags: { name: "no_access_check" } },
    ) as K6Response;
    const body = response.json();
    check(response, {
        "user without policy gets 200": (item: K6Response) => item.status === 200,
        "no-access returns zero rows": () => Array.isArray(body) && body.length === 0,
    });
}

/** Waits until the producer sync restores every local load-test table. */
function waitForLocalTables(token: string): void {
    const deadline = Date.now() + 300_000;
    let missing = LOAD_TABLES;

    while (Date.now() < deadline) {
        missing = LOAD_TABLES.filter((table) => {
            const response = http.get(`${POSTGREST_URL}/${table}?limit=1`, {
                headers: { Authorization: `Bearer ${token}`, "Accept-Profile": "pic" },
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
        `${POSTGREST_URL}/freshness?table=eq.protocolo_estado_diario&select=partition&order=partition.asc&limit=1`,
        { headers: { Authorization: `Bearer ${token}`, "Accept-Profile": "pic" }, tags: { name: "load_setup:fallback_dates" } },
    ) as K6Response;
    const rows = response.json() as { partition?: string }[];
    const partition = rows[0]?.partition || "";
    if (response.status !== 200 || !/^\d{8}$/.test(partition)) {
        throw new Error(`Could not discover oldest local protocol partition: ${response.body}`);
    }

    const date = new Date(`${partition.slice(0, 4)}-${partition.slice(4, 6)}-${partition.slice(6, 8)}T00:00:00Z`);
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
    const candidates = ROUTES.filter((route) => route.clients.indexOf(clientId) !== -1);
    const totalWeight = candidates.reduce((sum, route) => sum + route.weight, 0);
    let value = Math.random() * totalWeight;

    for (const route of candidates) {
        value -= route.weight;
        if (value < 0) return route;
    }

    throw new Error(`No load route is configured for client: ${clientId}`);
}

/** Records the response latency under its proxy source. */
function recordSourceDuration(response: K6Response): void {
    const source = response.headers["X-Source"] || response.headers["x-source"];
    if (source === "cache" || source === "postgrest" || source === "bigquery") {
        sourceDuration[source].add(response.timings.duration);
    }
}

/** Sends one request to a route and checks the status and body shape. */
function request(route: Route, token: string): void {
    const response = http.get(`${API_URL}${route.path}`, {
        headers: { Authorization: `Bearer ${token}`, Host: HOST, "Accept-Profile": route.profile },
        tags: { name: route.name, tag: route.name },
    }) as K6Response;
    recordSourceDuration(response);
    loadRequestFailed.add(response.status !== 200);
    const body = response.json();
    check(response, {
        [`${route.name} returned 200`]: (item: K6Response) => item.status === 200,
        [`${route.name} returned JSON array`]: () => route.checkBody(body),
    });
}

/** Sends a BigQuery-only protocol request and immediately verifies its cache hit. */
function requestFallbackPair(clientId: string, token: string, dates: string[]): void {
    const date = dates[Math.floor(Math.random() * dates.length)];
    const offset = Math.floor(Math.random() * FALLBACK_OFFSET_MAX);
    const path = `/protocolo_estado_diario?protocolo_data_referencia_particicao=eq.${date}&select=protocolo_id&limit=20&offset=${offset}`;
    const params = {
        headers: { Authorization: `Bearer ${token}`, Host: HOST, "Accept-Profile": "pic" },
        tags: { name: `bigquery_protocol:${clientId}` },
    };
    const first = http.get(`${API_URL}${path}`, params) as K6Response;
    recordSourceDuration(first);
    const second = http.get(`${API_URL}${path}`, params) as K6Response;
    recordSourceDuration(second);
    check(first, {
        "BigQuery protocol request returned 200": (item: K6Response) => item.status === 200,
        "BigQuery protocol request returns fallback data or a warm cache hit": (item: K6Response) => {
            const source = item.headers["X-Source"] || item.headers["x-source"];
            return source === "bigquery" || source === "cache";
        },
    });
    check(second, {
        "BigQuery protocol repeat returned 200": (item: K6Response) => item.status === 200,
        "BigQuery protocol repeat is cached": (item: K6Response) => item.headers["X-Source"] === "cache" || item.headers["x-source"] === "cache",
    });
}

/** Picks a route and a user, sends one request, then sleeps a random duration. */
export default function(data: SetupData): void {
    const vu = (__VU - 1) % CLIENT_IDS.length;
    const clientId = CLIENT_IDS[vu];
    const token = ensureToken(clientId, data.tokens[vu]);
    if (clientId !== "user-escola-3" && Math.random() < 0.25) {
        requestFallbackPair(clientId, token.token, data.fallbackDates);
    } else {
        request(pickRoute(clientId), token.token);
    }
    sleep(Math.random() * 3 + 0.5);
}
