import { consoleCall } from "./console.js";
import { rowsOf, safeJson } from "./http.js";
import { waitUntil } from "./wait.js";

const FINAL_STATUSES = ["SUCCESS", "ERROR", "CANCELLED", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"];

/**
 * Reads one run through the console, or undefined when the client doesn't see it.
 * @param {string} runId
 * @param {string} [client="admin"]
 * @returns {Record<string, unknown> | undefined}
 */
export function runOf(runId, client = "admin") {
    return rowsOf(consoleCall("GET", `/runs?id=eq.${encodeURIComponent(runId)}`, client))[0];
}

/**
 * Starts a run for the given schemas and tables, and returns its identifier, or null when the console refuses.
 * @param {string[]} schemas
 * @param {string[]} [tables=[]]
 * @param {string} [client="admin"]
 * @returns {string | null}
 */
export function startRun(schemas, tables = [], client = "admin") {
    const response = consoleCall("POST", "/rpc/start_sync", client, { schemas, tables });
    const runId = safeJson(response);

    return response.status === 200 && typeof runId === "string" ? runId : null;
}

/**
 * Waits until a run reaches a final status, and returns that status (undefined if it never did).
 * @param {string} runId
 * @param {number} [timeoutSeconds=600]
 * @param {string} [client="admin"]
 * @returns {string | undefined}
 */
export function waitForRun(runId, timeoutSeconds = 600, client = "admin") {
    waitUntil(
        timeoutSeconds,
        () => FINAL_STATUSES.includes(String(runOf(runId, client)?.status)),
        `run ${runId} to finish`,
    );

    const status = String(runOf(runId, client)?.status);

    return FINAL_STATUSES.includes(status) ? status : undefined;
}
