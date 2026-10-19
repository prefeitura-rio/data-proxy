/** @import { Response as K6Response } from 'k6/http' */
import http from "k6/http";
import { POSTGREST_URL } from "./env.js";
import { isRecord, memberOf, rowsOf, safeJson } from "./http.js";
import { clientToken } from "./auth.js";
import { rememberRequest } from "./log.js";
import { waitUntil } from "./wait.js";

/**
 * Sends one request to a schema of PostgREST with a token that the caller already has.
 * @param {string} profile
 * @param {Method} method
 * @param {string} path
 * @param {string} token
 * @param {unknown} [body]
 * @returns {K6Response}
 */
export function postgrestRequest(profile, method, path, token, body) {
    const response = http.request(method, `${POSTGREST_URL}${path}`, body === undefined ? null : JSON.stringify(body), {
        headers: {
            Authorization: `Bearer ${token}`,
            "Accept-Profile": profile,
            "Content-Profile": profile,
            "Content-Type": "application/json",
            Prefer: "return=representation",
        },
        tags: { name: `${profile}:${method}:${path.split("?")[0]}` },
    });

    return rememberRequest(response);
}

/**
 * Sends one request to a schema of PostgREST, as the console does with the token of a user.
 * @param {string} profile
 * @param {Method} method
 * @param {string} path
 * @param {string} clientId
 * @param {unknown} [body]
 * @returns {K6Response}
 */
export function postgrestCall(profile, method, path, clientId, body) {
    return postgrestRequest(profile, method, path, clientToken(clientId), body);
}

/**
 * Counts the rows of a read in one schema, or -1 when the read failed.
 * @param {string} profile
 * @param {string} path
 * @param {string} clientId
 * @returns {number}
 */
export function postgrestRows(profile, path, clientId) {
    const response = postgrestCall(profile, "GET", path, clientId);

    return response.status === 200 ? rowsOf(response).length : -1;
}

/**
 * Sends one request to the console schema of PostgREST.
 * @param {Method} method
 * @param {string} path
 * @param {string} clientId
 * @param {unknown} [body]
 * @returns {K6Response}
 */
export function consoleCall(method, path, clientId, body) {
    return postgrestCall("console", method, path, clientId, body);
}

/**
 * Counts the rows of a console read, or -1 when the read failed.
 * @param {string} path
 * @param {string} clientId
 * @returns {number}
 */
export function consoleRows(path, clientId) {
    return postgrestRows("console", path, clientId);
}

/**
 * Writes a value with its keys in order, so that two documents compare by content.
 * @param {unknown} value
 * @returns {string}
 */
export function canonical(value) {
    const sorted = /** @param {unknown} item @returns {unknown} */ (item) => {
        if (Array.isArray(item)) return item.map(sorted);

        if (isRecord(item)) {
            return Object.fromEntries(
                Object.entries(item)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([key, entry]) => [key, sorted(entry)]),
            );
        }

        return item;
    };

    return JSON.stringify(sorted(value));
}

/**
 * Reads the entry of one schema in the sync.json document, or undefined when it has none.
 * @param {string} name
 * @returns {unknown}
 */
export function documentOf(name) {
    return memberOf(safeJson(consoleCall("GET", "/rpc/sync_config", "admin")), "schemas")[name];
}

/**
 * Waits until the entry of a schema in sync.json has the given content, or is gone.
 * @param {string} name
 * @param {unknown} expected
 * @returns {boolean}
 */
export function documentBecomes(name, expected) {
    return waitUntil(10, () =>
        expected === undefined ? documentOf(name) === undefined : canonical(documentOf(name)) === canonical(expected),
    );
}
