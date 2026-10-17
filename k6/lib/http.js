/** @import { Response as K6Response } from 'k6/http' */
import http from "k6/http";
import { scenario } from "./check.js";
import { rememberRequest } from "./log.js";
import { API_URL, FULL_TABLE, HOST, POSTGREST_URL, PROXY_ADMIN_URL, SCHEMA } from "./env.js";

const ducklake = scenario("ducklake");

/**
 * @param {K6Response} r
 * @returns {unknown}
 */
export function safeJson(r) {
    if (r.status === 0 || !r.body) return null;

    try {
        return r.json();
    } catch {
        return null;
    }
}

/**
 * Asks the proxy to read the settings of the console now, and returns the answer.
 * @returns {K6Response}
 */
export function reloadProxy() {
    return rememberRequest(http.post(`${PROXY_ADMIN_URL}/reload`, null, { tags: { name: "proxy_reload" } }));
}

/**
 * Builds the authentication headers for a request through the proxy.
 * @param {string} token
 * @returns {Record<string, string>}
 */
export function authHeaders(token) {
    return {
        Authorization: `Bearer ${token}`,
        Host: HOST,
        "Accept-Profile": SCHEMA,
    };
}

/**
 * Sends a GET through the proxy with auth headers and optional extras.
 * @param {string} path
 * @param {string} token
 * @param {Record<string, string>} [extra={}]
 * @returns {K6Response}
 */
export function proxyGet(path, token, extra = {}) {
    return rememberRequest(
        http.get(`${API_URL}${path}`, {
            headers: { ...authHeaders(token), ...extra },
            tags: { name: `proxy:${path.split("?")[0]}` },
        }),
    );
}

/**
 * Sends a GET directly to PostgREST, bypassing the proxy.
 * @param {string} path
 * @param {string} token
 * @param {Record<string, string>} [extra={}]
 * @returns {K6Response}
 */
export function directPostgrest(path, token, extra = {}) {
    return rememberRequest(
        http.get(`${POSTGREST_URL}${path}`, {
            headers: {
                Authorization: `Bearer ${token}`,
                "Accept-Profile": SCHEMA,
                ...extra,
            },
            tags: { name: `postgrest:${path.split("?")[0]}` },
        }),
    );
}

/**
 * Reads one response header, handling case variants.
 * @param {K6Response} r
 * @param {string} name
 * @returns {string}
 */
export function headerOf(r, name) {
    const wanted = name.toLowerCase();
    const key = Object.keys(r.headers).find((k) => k.toLowerCase() === wanted);

    return key ? r.headers[key] : "";
}

/**
 * Reads the X-Cache header from a response.
 * @param {K6Response} r
 * @returns {string}
 */
export function cacheHeader(r) {
    return headerOf(r, "X-Cache");
}

/**
 * Reads the sources that served a response, from the X-Source header.
 * @param {K6Response} r
 * @returns {string}
 */
export function sourceHeader(r) {
    return headerOf(r, "X-Source");
}

/**
 * Reads the DuckLake snapshot a response was read from.
 * @param {K6Response} r
 * @returns {string}
 */
export function snapshotHeader(r) {
    return headerOf(r, "X-DuckLake-Snapshot");
}

/**
 * Serializes a value with sorted object keys: k6 does not keep the key order of parsed JSON.
 * @param {unknown} value
 * @returns {string}
 */
export function stable(value) {
    if (Array.isArray(value)) {
        return `[${value.map(stable).join(",")}]`;
    }

    if (isRecord(value)) {
        const entries = Object.keys(value)
            .sort()
            .map((key) => `${JSON.stringify(key)}:${stable(value[key])}`);

        return `{${entries.join(",")}}`;
    }

    return JSON.stringify(value);
}

/**
 * Reports whether a parsed JSON value is an object that isn't an array.
 * @param {unknown} value
 * @returns {value is Record<string, unknown>}
 */
export function isRecord(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Returns a field of a parsed JSON object when the field is an object, and an empty object otherwise.
 * @param {unknown} value
 * @param {string} key
 * @returns {Record<string, unknown>}
 */
export function memberOf(value, key) {
    const member = isRecord(value) ? value[key] : undefined;

    return isRecord(member) ? member : {};
}

/**
 * Returns the object rows of a parsed JSON array, or an empty array for any other value.
 * @param {unknown} body
 * @returns {Record<string, unknown>[]}
 */
export function recordsOf(body) {
    return Array.isArray(body) ? body.filter(isRecord) : [];
}

/**
 * Extracts the rows from a JSON array response, or an empty array.
 * @param {K6Response} r
 * @returns {Record<string, unknown>[]}
 */
export function rowsOf(r) {
    return recordsOf(safeJson(r));
}

/**
 * Reads the current DuckLake snapshot through the exposed RPC.
 * @param {string} token
 * @returns {string}
 */
export function snapshotValue(token) {
    const response = directPostgrest("/rpc/dl_latest_snapshot", token);
    const body = safeJson(response);

    ducklake.requirePrecondition(
        "the current DuckLake snapshot is readable",
        response.status === 200 && body !== null,
        { status: response.status, body: String(response.body).slice(0, 300) },
    );

    return JSON.stringify(body);
}

let uncachedCalls = 0;

/**
 * One read through the proxy that the cache never answers: the limit changes on each call.
 * @param {string} token
 * @returns {K6Response}
 */
export function readUncached(token) {
    uncachedCalls += 1;

    return proxyGet(`/${FULL_TABLE}?select=id&limit=${1 + (uncachedCalls % 50)}`, token);
}
