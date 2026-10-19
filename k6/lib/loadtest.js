/** @import { RequestParams, Response as K6Response } from 'k6/http' */
import http from "k6/http";
import { sleep } from "k6";
import {
    HEAVY_LIMIT_MIN,
    LOAD_TABLES,
    LOAD_TOKEN_REFRESH_SECONDS,
    PARTITION_COLUMN,
    POSTGREST_URL,
    SETUP_ATTEMPTS,
    SETUP_RETRY_SECONDS,
} from "./env.js";
import { headerOf, isRecord, recordsOf, safeJson } from "./http.js";
import { requestToken } from "./auth.js";
import { waitUntil } from "./wait.js";

/**
 * @param {string} field
 * @param {string[]} allowed
 * @returns {(body: unknown) => boolean}
 */
export function authorizedRows(field, allowed) {
    return /** @param {unknown} body @returns {boolean} */ (body) => {
        if (!Array.isArray(body) || body.length === 0) return false;

        return body.every((row) => isRecord(row) && allowed.includes(String(row[field])));
    };
}

/**
 * @param {string} field
 * @param {string[]} allowed
 * @returns {(body: unknown) => boolean}
 */
export function groupedCounts(field, allowed) {
    return /** @param {unknown} body @returns {boolean} */ (body) => {
        if (!Array.isArray(body) || body.length === 0) return false;

        return body.every(
            (row) =>
                isRecord(row) && allowed.includes(String(row[field])) && typeof row.count === "number" && row.count > 0,
        );
    };
}

/**
 * @param {string} field
 * @returns {(body: unknown) => boolean}
 */
export function sortedDescending(field) {
    return /** @param {unknown} body @returns {boolean} */ (body) => {
        const values = recordsOf(body).map((row) => String(row[field]));

        return values.every((value, index) => index === 0 || values[index - 1] >= value);
    };
}

/**
 * @param {string} status
 * @returns {(body: unknown) => boolean}
 */
export function hasStatus(status) {
    return /** @param {unknown} body @returns {boolean} */ (body) => {
        if (!Array.isArray(body) || body.length === 0) return false;

        return body.every((row) => isRecord(row) && isRecord(row.metadata) && row.metadata.status === status);
    };
}

/**
 * @param {...((body: unknown) => boolean)} checks
 * @returns {(body: unknown) => boolean}
 */
export function allOf(...checks) {
    return /** @param {unknown} body @returns {boolean} */ (body) => checks.every((item) => item(body));
}

/**
 * Fetches an OIDC access token with its expiry time.
 * @param {string} clientId
 * @returns {TokenData}
 */
export function tokenWithExpiry(clientId) {
    const { token, lifetime } = requestToken(clientId);

    return { token, expiresAt: Date.now() + lifetime * 1000 };
}

/**
 * Reports whether an answer is not JSON, which is what a proxy returns while a pod restarts.
 * @param {K6Response} response
 * @returns {boolean}
 */
function isTransient(response) {
    if (response.status === 0) return true;

    try {
        JSON.parse(String(response.body));

        return false;
    } catch {
        return true;
    }
}

/**
 * Sends a setup request again while the answer is a transient proxy error, such as during a PostgREST restart.
 * @param {string} url
 * @param {RequestParams} params
 * @returns {K6Response}
 */
export function setupGet(url, params) {
    let response = http.get(url, params);

    for (let attempt = 1; attempt < SETUP_ATTEMPTS && isTransient(response); attempt++) {
        sleep(SETUP_RETRY_SECONDS);
        response = http.get(url, params);
    }

    return response;
}

/**
 * Waits until the sync restores every local load-test table.
 * @param {string} token
 */
export function waitForLocalTables(token) {
    let missing = LOAD_TABLES;

    const populated = waitUntil(
        1200,
        () => {
            missing = LOAD_TABLES.filter((table) => {
                const response = setupGet(`${POSTGREST_URL}/${table}?limit=1`, {
                    headers: {
                        Authorization: `Bearer ${token}`,
                        "Accept-Profile": "test",
                    },
                    tags: { name: `load_setup:${table}` },
                });

                const body = safeJson(response);

                return response.status !== 200 || !Array.isArray(body) || body.length === 0;
            });

            return missing.length === 0;
        },
        "the sync to restore the load tables",
    );

    if (!populated) throw new Error(`Local tables were not populated before load: ${missing.join(", ")}`);
}

/**
 * Returns the two days immediately before the oldest DuckLake partition, read from a pinned snapshot.
 * @param {string} token
 * @returns {string[]}
 */
export function fallbackDates(token) {
    const headers = { Authorization: `Bearer ${token}`, "Accept-Profile": "test" };

    const snapshot = setupGet(`${POSTGREST_URL}/rpc/dl_latest_snapshot`, {
        headers,
        tags: { name: "load_setup:snapshot" },
    });

    if (snapshot.status !== 200) {
        throw new Error(`Could not read the latest DuckLake snapshot: ${snapshot.body}`);
    }

    const response = setupGet(
        `${POSTGREST_URL}/partitioned_table?select=${PARTITION_COLUMN}&order=${PARTITION_COLUMN}.asc&limit=1`,
        {
            headers: { ...headers, "X-DuckLake-Snapshot": String(safeJson(snapshot) ?? "") },
            tags: { name: "load_setup:fallback_dates" },
        },
    );

    const body = safeJson(response);
    const rows = recordsOf(body);
    const partition = String(rows[0]?.[PARTITION_COLUMN] || "");

    if (response.status !== 200 || !/^\d{4}-\d{2}-\d{2}$/.test(partition)) {
        throw new Error(`Could not discover the oldest DuckLake partition: ${response.body}`);
    }

    const date = new Date(`${partition}T00:00:00Z`);

    /** @type {string[]} */
    const dates = [];

    for (let days = 1; days <= 2; days++) {
        const older = new Date(date.getTime());
        older.setUTCDate(older.getUTCDate() - days);
        dates.push(older.toISOString().slice(0, 10));
    }

    return dates;
}

/**
 * Returns the first ids that the test user can read from big_table.
 * @param {string} token
 * @returns {string[]}
 */
export function visibleIds(token) {
    const response = setupGet(`${POSTGREST_URL}/big_table?select=id&limit=1000`, {
        headers: { Authorization: `Bearer ${token}`, "Accept-Profile": "test" },
        tags: { name: "load_setup:ids" },
    });

    const body = safeJson(response);
    const ids = recordsOf(body).map((row) => String(row.id));

    if (response.status !== 200 || ids.length === 0) {
        throw new Error(`Could not read any big_table id: ${response.body}`);
    }

    return ids;
}

/**
 * Returns the latest snapshot and the older snapshots that big_table can still be read at.
 * @param {string} token
 * @returns {string[]}
 */
export function readableSnapshots(token) {
    const headers = { Authorization: `Bearer ${token}`, "Accept-Profile": "test" };

    const latest = setupGet(`${POSTGREST_URL}/rpc/dl_latest_snapshot`, {
        headers,
        tags: { name: "load_setup:snapshot" },
    });

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
            });

            const body = safeJson(response);

            return response.status === 200 && Array.isArray(body) && body.length > 0;
        });
}

/** @type {Record<string, TokenData>} */
const vuTokens = {};

/**
 * Returns a valid token for the given client, refreshing it before it expires.
 * @param {string} clientId
 * @param {TokenData} current
 * @returns {TokenData}
 */
export function ensureToken(clientId, current) {
    const known = vuTokens[clientId] ?? current;

    if (Date.now() < known.expiresAt - LOAD_TOKEN_REFRESH_SECONDS * 1000) {
        vuTokens[clientId] = known;

        return known;
    }

    const next = tokenWithExpiry(clientId);
    vuTokens[clientId] = next;

    return next;
}

/**
 * Classifies the source header without treating a cache hit as a source.
 * @param {K6Response} response
 * @returns {"ducklake" | "fallback" | null}
 */
export function sourceKind(response) {
    const source = headerOf(response, "X-Source");
    if (source === "ducklake") return "ducklake";
    if (source.includes("ducklake+")) return "fallback";

    return null;
}

/**
 * Classifies a response as a cache hit, or by the source that served it.
 * @param {K6Response} response
 * @returns {"cache" | "ducklake" | "fallback" | null}
 */
export function sourceOf(response) {
    if (headerOf(response, "X-Cache") === "HIT") return "cache";

    return sourceKind(response);
}

const DURATION_UNIT_SECONDS = new Map([
    ["s", 1],
    ["m", 60],
    ["h", 3600],
]);

/**
 * @param {string} value
 * @returns {number}
 */
export function durationSeconds(value) {
    const match = /^(\d+)(s|m|h)$/.exec(value);
    if (!match) throw new Error(`Unsupported profile duration: ${value}`);
    const scale = DURATION_UNIT_SECONDS.get(match[2]);
    if (scale === undefined) throw new Error(`Unsupported profile duration unit: ${match[2]}`);

    return Number(match[1]) * scale;
}

/**
 * @template T
 * @param {T[]} items
 * @returns {T}
 */
export function randomItem(items) {
    return items[Math.floor(Math.random() * items.length)];
}

/**
 * @returns {number}
 */
export function uniqueLimit() {
    return HEAVY_LIMIT_MIN + Math.floor(Math.random() * HEAVY_LIMIT_MIN);
}
