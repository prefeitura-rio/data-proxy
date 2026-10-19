/** @import { Response as K6Response } from 'k6/http' */
import http from "k6/http";
import { check, sleep } from "k6";
import { Kubernetes } from "k6/x/kubernetes";
import { Counter, Rate, Trend } from "k6/metrics";
import {
    allOf,
    API_URL,
    authHeaders,
    authorizedRows,
    CONSOLE_ADMIN_CLIENT_ID,
    CONSOLE_VIEWER_CLIENT_ID,
    durationSeconds,
    ensureToken,
    fallbackDates,
    FULL_SOURCE,
    groupedCounts,
    hasStatus,
    headerOf,
    HEAVY_LIMIT_MIN,
    info,
    inStep,
    NAMESPACE,
    OIDC_NO_POLICY_CLIENT_ID,
    OIDC_USER_CLIENT_ID,
    PARTITION_COLUMN,
    POLICY_CLIENT_ID,
    POSTGREST_URL,
    randomItem,
    recordsOf,
    readableSnapshots,
    safeJson,
    scalingReplicaCounts,
    SCHEMA,
    seedAccessPolicy,
    setScenario,
    setupGet,
    sortedDescending,
    sourceKind,
    sourceOf,
    tokenWithExpiry,
    uniqueLimit,
    visibleIds,
    waitForLocalTables,
    warn,
} from "./lib/index.js";

setScenario("load");
const K6_PROFILE = __ENV.K6_PROFILE || "smoke";
const THRESHOLD_MODE = __ENV.K6_THRESHOLD_MODE || "strict";
const FALLBACK_LIMIT_MAX = 20;
const DEFAULT_BOTTLENECK_SHARE = THRESHOLD_MODE === "capacity" ? 0.05 : 0.01;
const DEFAULT_FALLBACK_SHARE = THRESHOLD_MODE === "capacity" ? 0.05 : 0.01;
const DEFAULT_PEAK_RATE = 25;
const DEFAULT_MAX_VUS_MULTIPLIER = THRESHOLD_MODE === "capacity" ? 6 : 3;
const BOTTLENECK_SHARE = Number(__ENV.K6_BOTTLENECK_SHARE || String(DEFAULT_BOTTLENECK_SHARE));
const FALLBACK_SHARE = Number(__ENV.K6_FALLBACK_SHARE || String(DEFAULT_FALLBACK_SHARE));
const CONSOLE_SHARE = Number(__ENV.K6_CONSOLE_SHARE || "0.05");
const POLICY_SHARE = Number(__ENV.K6_POLICY_SHARE || "0.01");
const PEAK_RATE = Number(__ENV.K6_PEAK_RATE || String(DEFAULT_PEAK_RATE));
const MAX_VUS_MULTIPLIER = Number(__ENV.K6_MAX_VUS_MULTIPLIER || String(DEFAULT_MAX_VUS_MULTIPLIER));

if (
    !Number.isFinite(PEAK_RATE) ||
    PEAK_RATE <= 0 ||
    !Number.isFinite(BOTTLENECK_SHARE) ||
    BOTTLENECK_SHARE < 0 ||
    !Number.isFinite(FALLBACK_SHARE) ||
    FALLBACK_SHARE < 0 ||
    !Number.isFinite(CONSOLE_SHARE) ||
    CONSOLE_SHARE < 0 ||
    !Number.isFinite(POLICY_SHARE) ||
    POLICY_SHARE < 0 ||
    BOTTLENECK_SHARE + FALLBACK_SHARE + CONSOLE_SHARE + POLICY_SHARE >= 1 ||
    !Number.isInteger(MAX_VUS_MULTIPLIER) ||
    MAX_VUS_MULTIPLIER < 1
) {
    throw new Error("Invalid k6 load parameters");
}

if (THRESHOLD_MODE !== "strict" && THRESHOLD_MODE !== "capacity") {
    throw new Error("Invalid k6 threshold mode");
}

const CLIENT_IDS = [OIDC_USER_CLIENT_ID, OIDC_NO_POLICY_CLIENT_ID, OIDC_USER_CLIENT_ID];
const PEAK_ITERATIONS = Math.round(PEAK_RATE / (1 + FALLBACK_SHARE));

/**
 * @param {number} fraction
 * @returns {number}
 */
function iterationsForPeak(fraction) {
    return Math.max(1, Math.round(PEAK_ITERATIONS * fraction));
}

/** @type {Record<string, Profile>} */
const PROFILES = {
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
        maxVUs: PEAK_ITERATIONS * MAX_VUS_MULTIPLIER,
        duration: "33m",
        stages: [
            { target: iterationsForPeak(0.25), duration: "120s" },
            { target: iterationsForPeak(0.5), duration: "300s" },
            { target: iterationsForPeak(0.75), duration: "300s" },
            { target: iterationsForPeak(1), duration: "300s" },
            { target: iterationsForPeak(1), duration: "900s" },
            { target: 0, duration: "60s" },
        ],
    },
};

if (!(K6_PROFILE in PROFILES)) {
    throw new Error(`Unknown K6_PROFILE: ${K6_PROFILE}`);
}

const profile = PROFILES[K6_PROFILE];
const loadRequestFailed = new Rate("load_request_failed");
const loadCheckFailed = new Counter("load_check_failed");

const sourceDuration = {
    cache: new Trend("cache_duration_ms"),
    ducklake: new Trend("ducklake_duration_ms"),
    fallback: new Trend("fallback_duration_ms"),
};

const consoleDuration = new Trend("console_duration_ms");
const policyDuration = new Trend("policy_duration_ms");
const ducklakeHeavyDuration = new Trend("ducklake_heavy_duration_ms");
const ducklakeSelectiveDuration = new Trend("ducklake_selective_duration_ms");
const ducklakePinnedDuration = new Trend("ducklake_pinned_duration_ms");

/** @type {Record<string, string[]>} */
const stackThresholds = {
    checks: ["rate==1"],
    load_request_failed: ["rate<0.01"],
    http_req_failed: ["rate<0.01"],
    cache_duration_ms: ["p(95)<50", "p(99)<100"],
    ducklake_duration_ms: ["p(95)<300", "p(99)<600"],
    console_duration_ms: ["p(95)<1000", "p(99)<2000"],
    policy_duration_ms: ["p(95)<500", "p(99)<1000"],
};

/** @type {Record<string, string[]>} */
const clientRiskThresholds = {
    fallback_duration_ms: ["p(95)<6000"],
    ducklake_heavy_duration_ms: ["p(95)<2000", "p(99)<5000"],
    ducklake_selective_duration_ms: ["p(95)<2000", "p(99)<5000"],
    ducklake_pinned_duration_ms: ["p(95)<2000", "p(99)<5000"],
};

const gateClientRisk = (__ENV.K6_GATE_CLIENT_RISK || "false") === "true";

/** @type {Record<string, string[]>} */
const sourceThresholds = gateClientRisk ? { ...stackThresholds, ...clientRiskThresholds } : { ...stackThresholds };
const scalingEvents = new Counter("autoscaling_events");

if (THRESHOLD_MODE === "capacity") {
    sourceThresholds.checks = ["rate>0.95"];
    sourceThresholds.load_request_failed = ["rate<0.05"];
    sourceThresholds.http_req_failed = ["rate<0.05"];
}

const runsScalingObserver = profile.executor !== "ramping-vus";
const isArrivalRate = profile.executor !== "ramping-vus";
const gatesAutoscaling = THRESHOLD_MODE === "capacity";

if (isArrivalRate) {
    sourceThresholds.dropped_iterations = ["count==0"];
}

if (runsScalingObserver && gatesAutoscaling) {
    sourceThresholds.autoscaling_events = ["count>0"];
}

/**
 * @returns {Record<string, unknown>}
 */
function buildScenarios() {
    /** @type {Record<string, unknown>} */
    const built = {
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

const ACCESS_POLICY_ROWS = [
    { subject: "test_user_1", unit_type: "unit", unit_id: "unit_1" },
    { subject: "test_user_1", unit_type: "region", unit_id: "region_1" },
    { subject: "test_user_1", unit_type: "group", unit_id: "group_1" },
];

/** @type {Route[]} */
const ROUTES = [
    {
        profile: "test",
        path: "/full_table?unit_id=eq.unit_1&limit=20",
        name: "test_full_table_unit_1",
        expectedSource: "ducklake",
        weight: 30,
        clients: ["api"],
        checkBody: authorizedRows("unit_id", ["unit_1"]),
    },
    {
        profile: "test",
        path: "/full_table?unit_id=eq.unit_2&limit=20",
        name: "test_full_table_unit_2",
        expectedSource: "ducklake",
        weight: 30,
        clients: ["api_no_policy"],
        checkBody: authorizedRows("unit_id", ["unit_2"]),
    },
    {
        profile: "test",
        path: "/multi_rls_table?region_id=eq.region_1&limit=20",
        name: "test_multi_rls_table_region_1",
        expectedSource: "ducklake",
        weight: 20,
        clients: ["api"],
        checkBody: authorizedRows("region_id", ["region_1"]),
    },
    {
        profile: "test",
        path: "/multi_rls_table?group_id=eq.group_1&limit=20",
        name: "test_multi_rls_table_group_1",
        expectedSource: "ducklake",
        weight: 15,
        clients: ["api"],
        checkBody: authorizedRows("group_id", ["group_1"]),
    },
    {
        profile: "test",
        path: "/partitioned_table?unit_id=eq.unit_1&limit=20&order=date.desc",
        name: "test_partitioned_table_unit_1",
        expectedSource: "fallback",
        weight: 15,
        clients: ["api"],
        checkBody: authorizedRows("unit_id", ["unit_1"]),
    },
    {
        profile: "test",
        path: "/multi_rls_table?select=region_id,group_id,count()",
        name: "test_multi_rls_table_group_by",
        expectedSource: "ducklake",
        heavy: true,
        weight: 5,
        clients: ["api"],
        checkBody: groupedCounts("region_id", ["region_1", "region_2", "region_3"]),
    },
    {
        profile: "test",
        path: "/big_table?select=metadata->>status,count()",
        name: "test_big_table_group_by_json",
        expectedSource: "ducklake",
        heavy: true,
        weight: 5,
        clients: ["api"],
        checkBody: groupedCounts("status", ["active", "inactive", "pending"]),
    },
    {
        profile: "test",
        path: "/big_table?select=id,name,unit_id,metadata&order=name.desc",
        name: "test_big_table_scan_sorted",
        expectedSource: "ducklake",
        heavy: true,
        weight: 5,
        clients: ["api"],
        checkBody: allOf(authorizedRows("unit_id", ["unit_1"]), sortedDescending("name")),
    },
    {
        profile: "test",
        path: "/big_table?select=id,unit_id,metadata&metadata->>status=eq.active&order=id.desc",
        name: "test_big_table_json_filter_sorted",
        expectedSource: "ducklake",
        heavy: true,
        weight: 5,
        clients: ["api"],
        checkBody: allOf(authorizedRows("unit_id", ["unit_1"]), hasStatus("active"), sortedDescending("id")),
    },
];

const SECOND_ACCESS_POLICY_ROWS = [{ subject: "test_user_2", unit_type: "unit", unit_id: "unit_2" }];

/**
 * Verifies that a user without a policy gets a 200 with zero rows.
 */
function verifyNoAccess() {
    const token = tokenWithExpiry(OIDC_NO_POLICY_CLIENT_ID);

    const response = setupGet(`${API_URL}/full_table?limit=1`, {
        headers: authHeaders(token.token),
        tags: { name: "no_access_check" },
    });

    const body = safeJson(response);

    check(response, {
        "user without policy gets 200": /** @param {K6Response} item */ (item) => item.status === 200,
        "no-access returns zero rows": () => Array.isArray(body) && body.length === 0,
        "no-access queries no source": () => headerOf(response, "X-Source") === "",
        "no-access response omits snapshot": () => headerOf(response, "X-DuckLake-Snapshot") === "",
    });
}

/**
 * Reads every console route once with each client that may read it, and stops the run if one of them fails.
 * @param {Record<string, TokenData>} tokens
 */
function verifyConsole(tokens) {
    /** @type {string[]} */
    const failures = [];

    for (const route of CONSOLE_ROUTES) {
        for (const clientId of route.clients) {
            const response = setupGet(`${POSTGREST_URL}${route.path}`, {
                headers: { Authorization: `Bearer ${tokens[clientId].token}`, "Accept-Profile": "console" },
                tags: { name: `load_setup:console:${route.name}` },
            });

            if (response.status !== 200) failures.push(`${route.name} as ${clientId}: ${response.status}`);
        }
    }

    if (failures.length > 0) throw new Error(`The console routes of the load test fail: ${failures.join("; ")}`);
}

/**
 * Fetches tokens for every test user before the load test starts.
 * @returns {SetupData}
 */
export function setup() {
    return inStep("load", "setup", prepare);
}

/**
 * @returns {SetupData}
 */
function prepare() {
    info("load_started", { profile: K6_PROFILE, executor: profile.executor, duration: profile.duration });
    const k8s = new Kubernetes();
    seedAccessPolicy(ACCESS_POLICY_ROWS);
    const tokens = CLIENT_IDS.map((id) => tokenWithExpiry(id));

    const otherTokens = Object.fromEntries(
        [CONSOLE_VIEWER_CLIENT_ID, CONSOLE_ADMIN_CLIENT_ID, POLICY_CLIENT_ID].map((id) => [id, tokenWithExpiry(id)]),
    );

    waitForLocalTables(tokens[0].token);
    verifyNoAccess();
    verifyConsole(otherTokens);
    seedAccessPolicy(SECOND_ACCESS_POLICY_ROWS);

    /** @type {Record<string, string[]>} */
    const idsByClient = {};

    CLIENT_IDS.forEach((clientId, index) => {
        if (!(clientId in idsByClient)) {
            idsByClient[clientId] = visibleIds(tokens[index].token);
        }
    });

    return {
        tokens,
        fallbackDates: fallbackDates(tokens[0].token),
        idsByClient,
        snapshots: readableSnapshots(tokens[0].token),
        otherTokens,
        replicaBaseline: scalingReplicaCounts(k8s),
    };
}

/**
 * Picks a weighted route that the selected client can read.
 * @param {string} clientId
 * @returns {Route}
 */
function pickRoute(clientId) {
    const candidates = ROUTES.filter((route) => !route.heavy && route.clients.indexOf(clientId) !== -1);
    const totalWeight = candidates.reduce((sum, route) => sum + route.weight, 0);
    let value = Math.random() * totalWeight;

    for (const route of candidates) {
        value -= route.weight;
        if (value < 0) return route;
    }

    throw new Error(`No load route is configured for client: ${clientId}`);
}

/**
 * Records the response latency under its source.
 * @param {K6Response} response
 * @param {Trend} [ducklakeTrend]
 */
function recordSourceDuration(response, ducklakeTrend) {
    const source = sourceOf(response);

    if (source === "ducklake" && ducklakeTrend) {
        ducklakeTrend.add(response.timings.duration);
    } else if (source !== null) {
        sourceDuration[source].add(response.timings.duration);
    }
}

/**
 * How many failed checks one virtual user prints, so a bad run doesn't flood the log.
 */
const FAILURE_LOG_LIMIT = 25;
let failuresLogged = 0;

/**
 * @param {string} route
 * @param {string} reason
 * @param {K6Response} response
 */
function recordLoadCheckFailure(route, reason, response) {
    if (failuresLogged === FAILURE_LOG_LIMIT) {
        failuresLogged += 1;
        warn("load_check_failures_suppressed", { limit: FAILURE_LOG_LIMIT, note: "the metrics keep counting" });
    }

    if (failuresLogged < FAILURE_LOG_LIMIT) {
        failuresLogged += 1;

        warn("load_check_failed", {
            route,
            reason,
            status: response.status,
            source: headerOf(response, "X-Source") || "none",
            duration_ms: Math.round(response.timings.duration),
            body: String(response.body).slice(0, 160),
        });
    }

    loadCheckFailed.add(1, {
        route,
        reason,
        status: String(response.status),
        source: headerOf(response, "X-Source") || "none",
    });
}

/**
 * Sends one request to a route and checks the status and body shape.
 * @param {Route} route
 * @param {string} token
 */
function request(route, token) {
    const path = route.heavy
        ? `${route.path}&limit=${HEAVY_LIMIT_MIN + Math.floor(Math.random() * HEAVY_LIMIT_MIN)}`
        : route.path;

    const response = http.get(`${API_URL}${path}`, {
        headers: { ...authHeaders(token), "Accept-Profile": route.profile },
        tags: { name: route.name, tag: route.name },
    });

    recordSourceDuration(response, route.heavy ? ducklakeHeavyDuration : undefined);
    loadRequestFailed.add(response.status !== 200);
    const body = safeJson(response);
    const statusOk = response.status === 200;
    const bodyOk = route.checkBody(body);
    const sourceOk = sourceKind(response) === route.expectedSource;
    if (!statusOk) recordLoadCheckFailure(route.name, "status", response);
    if (!bodyOk) recordLoadCheckFailure(route.name, "body", response);
    if (!sourceOk) recordLoadCheckFailure(route.name, "source", response);

    check(response, {
        [`${route.name} returned 200`]: () => statusOk,
        [`${route.name} returned JSON array`]: () => bodyOk,
        [`${route.name} reports ${route.expectedSource}`]: () => sourceOk,
    });
}

/**
 * Requests an older partition from a configured fallback source and verifies the repeat is cached.
 * @param {string} clientId
 * @param {string} token
 * @param {string[]} dates
 */
function requestFallbackPair(clientId, token, dates) {
    const date = dates[Math.floor(Math.random() * dates.length)];
    const limit = Math.floor(Math.random() * FALLBACK_LIMIT_MAX) + 1;
    const path = `/partitioned_table?date=eq.${date}&select=id,date,unit_id&limit=${limit}`;

    const params = {
        headers: authHeaders(token),
        tags: { name: `fallback:${clientId}` },
    };

    const first = http.get(`${API_URL}${path}`, params);
    recordSourceDuration(first);
    loadRequestFailed.add(first.status !== 200);
    const firstBody = safeJson(first);
    const second = http.get(`${API_URL}${path}`, params);
    recordSourceDuration(second);
    loadRequestFailed.add(second.status !== 200);
    const firstStatusOk = first.status === 200;
    const firstSourceOk = sourceOf(first) === "fallback" || sourceOf(first) === "cache";
    const firstPartitionOk = authorizedRows(PARTITION_COLUMN, [date])(firstBody);
    const expectedUnit = clientId === OIDC_NO_POLICY_CLIENT_ID ? "unit_2" : "unit_1";
    const firstRlsOk = authorizedRows("unit_id", [expectedUnit])(firstBody);
    const secondStatusOk = second.status === 200;
    const secondCacheOk = sourceOf(second) === "cache";
    const secondBodyOk = second.body === first.body;
    if (!firstStatusOk) recordLoadCheckFailure(`fallback:${clientId}`, "status", first);
    if (!firstSourceOk) recordLoadCheckFailure(`fallback:${clientId}`, "source", first);
    if (!firstPartitionOk) recordLoadCheckFailure(`fallback:${clientId}`, "partition", first);
    if (!firstRlsOk) recordLoadCheckFailure(`fallback:${clientId}`, "rls", first);
    if (!secondStatusOk) recordLoadCheckFailure(`fallback:${clientId}`, "repeat_status", second);
    if (!secondCacheOk) recordLoadCheckFailure(`fallback:${clientId}`, "repeat_cache", second);
    if (!secondBodyOk) recordLoadCheckFailure(`fallback:${clientId}`, "repeat_body", second);

    check(first, {
        "Fallback request returned 200": () => firstStatusOk,
        "Fallback request reaches the configured source or a warm cache hit": () => firstSourceOk,
        "Fallback rows belong to the selected partition": () => firstPartitionOk,
        "Fallback rows obey RLS": () => firstRlsOk,
    });

    check(second, {
        "Fallback repeat returned 200": () => secondStatusOk,
        "Fallback repeat is cached": () => secondCacheOk,
        "Fallback repeat returns the same rows": () => secondBodyOk,
    });
}

/**
 * Picks a route and a user, sends one request, then sleeps a random duration.
 * @param {SetupData} data
 */
export default function (data) {
    const vu = (__VU - 1) % CLIENT_IDS.length;
    const clientId = CLIENT_IDS[vu];
    const token = ensureToken(clientId, data.tokens[vu]);
    const pick = Math.random();

    if (pick < BOTTLENECK_SHARE) {
        bottleneckCase(clientId, token.token, data);
    } else if (pick < BOTTLENECK_SHARE + FALLBACK_SHARE) {
        requestFallbackPair(clientId, token.token, data.fallbackDates);
    } else if (pick < BOTTLENECK_SHARE + FALLBACK_SHARE + CONSOLE_SHARE) {
        consoleTraffic(data);
    } else if (pick < BOTTLENECK_SHARE + FALLBACK_SHARE + CONSOLE_SHARE + POLICY_SHARE) {
        policyTraffic(data);
    } else {
        request(pickRoute(clientId), token.token);
    }

    if (profile.executor === "ramping-vus") sleep(Math.random() * 3 + 0.5);
}

/**
 * Polls replica counts throughout capacity runs, and records autoscaling increases.
 * @param {SetupData} data
 */
export function observeScaling(data) {
    inStep("load", "observeScaling", () => observe(data));
}

/**
 * @param {SetupData} data
 */
function observe(data) {
    const k8s = new Kubernetes();
    const names = Object.keys(data.replicaBaseline);

    /** @type {Record<string, boolean>} */
    const observed = {};
    const deadline = Date.now() + durationSeconds(profile.duration || "0s") * 1000;

    while (Date.now() < deadline) {
        for (const name of names) {
            try {
                const deployment = k8s.get("Deployment.apps", name, NAMESPACE);
                const replicas = deployment.spec.replicas || 0;

                if (
                    replicas > data.replicaBaseline[name] &&
                    (deployment.status?.availableReplicas || 0) >= replicas &&
                    !observed[name]
                ) {
                    observed[name] = true;
                    scalingEvents.add(1, { deployment: name });
                    info("autoscaling_observed", { deployment: name, from: data.replicaBaseline[name], to: replicas });
                }
            } catch {
                continue;
            }
        }

        sleep(5);
    }
}

/**
 * Looks up one row by id. Postgres filters the rows only after DuckLake returns
 * every visible row, so this latency grows with the table size and not with the answer.
 * @param {string} token
 * @param {string[]} ids
 * @param {string} expectedUnit
 */
function selectiveLookup(token, ids, expectedUnit) {
    const id = randomItem(ids);

    const response = http.get(`${API_URL}/big_table?select=id,unit_id&id=eq.${id}&limit=${uniqueLimit()}`, {
        headers: authHeaders(token),
        tags: { name: "selective_lookup" },
    });

    recordSourceDuration(response, ducklakeSelectiveDuration);
    loadRequestFailed.add(response.status !== 200);
    const body = safeJson(response);
    const statusOk = response.status === 200;
    const rows = recordsOf(body);
    const bodyOk = rows.length === 1 && String(rows[0].id) === id && rows[0].unit_id === expectedUnit;
    const sourceOk = headerOf(response, "X-Source") === "ducklake";
    if (!statusOk) recordLoadCheckFailure("selective_lookup", "status", response);
    if (!bodyOk) recordLoadCheckFailure("selective_lookup", "body", response);
    if (!sourceOk) recordLoadCheckFailure("selective_lookup", "source", response);

    check(response, {
        "selective lookup returned 200": () => statusOk,
        "selective lookup returned the requested row": () => bodyOk,
        "selective lookup reads DuckLake": () => sourceOk,
    });
}

/**
 * Reads big_table at the latest and at older pinned snapshots.
 * @param {string} token
 * @param {string[]} snapshots
 * @param {string} expectedUnit
 */
function pinnedSnapshot(token, snapshots, expectedUnit) {
    const snapshot = randomItem(snapshots);

    const response = http.get(`${API_URL}/big_table?unit_id=eq.${expectedUnit}&limit=${uniqueLimit()}`, {
        headers: { ...authHeaders(token), "X-DuckLake-Snapshot": snapshot },
        tags: { name: "pinned_snapshot" },
    });

    recordSourceDuration(response, ducklakePinnedDuration);
    loadRequestFailed.add(response.status !== 200);
    const body = safeJson(response);
    const statusOk = response.status === 200;
    const bodyOk = authorizedRows("unit_id", [expectedUnit])(body);
    const sourceOk = headerOf(response, "X-Source") === "ducklake";
    const snapshotOk = headerOf(response, "X-DuckLake-Snapshot") === snapshot;
    if (!statusOk) recordLoadCheckFailure("pinned_snapshot", "status", response);
    if (!bodyOk) recordLoadCheckFailure("pinned_snapshot", "body", response);
    if (!sourceOk) recordLoadCheckFailure("pinned_snapshot", "source", response);
    if (!snapshotOk) recordLoadCheckFailure("pinned_snapshot", "snapshot", response);

    check(response, {
        "pinned snapshot returned 200": () => statusOk,
        "pinned snapshot returned authorized rows": () => bodyOk,
        "pinned snapshot reads DuckLake only": () => sourceOk,
        "pinned snapshot reports the requested snapshot": () => snapshotOk,
    });
}

/**
 * Sends one bottleneck case: half heavy queries, a quarter each of lookups and pinned reads.
 * @param {string} clientId
 * @param {string} token
 * @param {SetupData} data
 */
function bottleneckCase(clientId, token, data) {
    const expectedUnit = clientId === OIDC_NO_POLICY_CLIENT_ID ? "unit_2" : "unit_1";
    const pick = Math.random();
    const heavyRoutes = ROUTES.filter((route) => route.heavy && route.clients.includes(clientId));

    if (pick < 0.5 && heavyRoutes.length > 0) {
        request(randomItem(heavyRoutes), token);
    } else if (pick < 0.75) {
        selectiveLookup(token, data.idsByClient[clientId], expectedUnit);
    } else {
        pinnedSnapshot(token, data.snapshots, expectedUnit);
    }
}

const BOTH_CONSOLE_CLIENTS = [CONSOLE_VIEWER_CLIENT_ID, CONSOLE_ADMIN_CLIENT_ID];

/**
 * What the web app reads from the console schema of PostgREST: the tabs Sync, Config and Query. Reads only.
 * @type {ConsoleRoute[]}
 */
const CONSOLE_ROUTES = [
    {
        name: "overview",
        path: "/overview",
        clients: BOTH_CONSOLE_CLIENTS,
        nonEmpty: true,
        checkRows: (rows) => rows.some((row) => row.name === SCHEMA),
    },
    { name: "runs", path: "/runs?order=created_at.desc&limit=20", clients: BOTH_CONSOLE_CLIENTS, nonEmpty: true },
    { name: "tasks", path: "/tasks?limit=50", clients: BOTH_CONSOLE_CLIENTS, nonEmpty: true },
    { name: "logs", path: "/logs?limit=50", clients: BOTH_CONSOLE_CLIENTS, nonEmpty: false },
    { name: "errors", path: "/errors?limit=20", clients: BOTH_CONSOLE_CLIENTS, nonEmpty: false },
    { name: "health", path: "/health", clients: BOTH_CONSOLE_CLIENTS, nonEmpty: true },
    { name: "synced", path: "/synced?limit=50", clients: BOTH_CONSOLE_CLIENTS, nonEmpty: true },
    { name: "schedules", path: "/schedules", clients: BOTH_CONSOLE_CLIENTS, nonEmpty: true },
    { name: "queues", path: "/queues", clients: BOTH_CONSOLE_CLIENTS, nonEmpty: true },
    {
        name: "schemas",
        path: "/schemas?select=name",
        clients: BOTH_CONSOLE_CLIENTS,
        nonEmpty: true,
        checkRows: (rows) => rows.some((row) => row.name === SCHEMA),
    },
    { name: "tables", path: "/tables?select=name,schema&limit=50", clients: BOTH_CONSOLE_CLIENTS, nonEmpty: true },
    {
        name: "table_files",
        path: `/rpc/table_files?schema_name=${SCHEMA}&table_name=${FULL_SOURCE}`,
        clients: [CONSOLE_ADMIN_CLIENT_ID],
        nonEmpty: true,
        checkRows: (rows) => rows.every((row) => String(row.path).endsWith(".parquet")),
    },
];

/**
 * Sends one read to the console schema of PostgREST with the token of a console client, as the web app does.
 * @param {ConsoleRoute} route
 * @param {string} clientId
 * @param {string} token
 */
function consoleRead(route, clientId, token) {
    const response = http.get(`${POSTGREST_URL}${route.path}`, {
        headers: { Authorization: `Bearer ${token}`, "Accept-Profile": "console" },
        tags: { name: `console:${route.name}`, tag: `console:${route.name}` },
    });

    consoleDuration.add(response.timings.duration);
    loadRequestFailed.add(response.status !== 200);
    const body = safeJson(response);
    const rows = recordsOf(body);
    const statusOk = response.status === 200;
    const shapeOk = Array.isArray(body);
    const rowsOk = (!route.nonEmpty || rows.length > 0) && (route.checkRows?.(rows) ?? true);
    const name = `console:${route.name}:${clientId}`;
    if (!statusOk) recordLoadCheckFailure(name, "status", response);
    if (!shapeOk) recordLoadCheckFailure(name, "body", response);
    if (statusOk && shapeOk && !rowsOk) recordLoadCheckFailure(name, "rows", response);

    check(response, {
        "console read returned 200": () => statusOk,
        "console read returned a JSON array": () => shapeOk,
        "console read returned the expected rows": () => rowsOk,
    });
}

/**
 * Picks a console route that the client may read, and reads it.
 * @param {SetupData} data
 */
function consoleTraffic(data) {
    const clientId = Math.random() < 0.7 ? CONSOLE_VIEWER_CLIENT_ID : CONSOLE_ADMIN_CLIENT_ID;
    const token = ensureToken(clientId, data.otherTokens[clientId]);
    consoleRead(randomItem(CONSOLE_ROUTES.filter((route) => route.clients.includes(clientId))), clientId, token.token);
}

/**
 * Reads the access policy of the schema through the proxy, as a service that manages its policy does. Reads only.
 * @param {SetupData} data
 */
function policyTraffic(data) {
    const token = ensureToken(POLICY_CLIENT_ID, data.otherTokens[POLICY_CLIENT_ID]);

    const response = http.get(`${API_URL}/access_policy?limit=20`, {
        headers: authHeaders(token.token),
        tags: { name: "policy_read", tag: "policy_read" },
    });

    policyDuration.add(response.timings.duration);
    loadRequestFailed.add(response.status !== 200);
    const body = safeJson(response);
    const statusOk = response.status === 200;
    const bodyOk = Array.isArray(body) && body.length > 0;
    if (!statusOk) recordLoadCheckFailure("policy_read", "status", response);
    if (statusOk && !bodyOk) recordLoadCheckFailure("policy_read", "body", response);

    check(response, {
        "policy read returned 200": () => statusOk,
        "policy read returned the policy rows": () => bodyOk,
    });
}
