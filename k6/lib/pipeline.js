/** @import { Response as K6Response } from 'k6/http' */
import http from "k6/http";
import { API_URL, PARTITION_COLUMN, PARTITIONED_TABLE, SYNCED_PARTITIONS, TABLES } from "./env.js";
import { waitUntil } from "./wait.js";
import { authHeaders, proxyGet, rowsOf, safeJson } from "./http.js";
import { userToken } from "./auth.js";

/**
 * Builds a PostgREST metric request that reads a path through the proxy.
 * @param {string} metric
 * @param {string} label
 * @param {string} path
 * @param {string} token
 * @param {(response: K6Response) => unknown} extract
 * @returns {MetricRequest}
 */
export function postgrestMetric(metric, label, path, token, extract) {
    return {
        metric,
        label,
        url: `${API_URL}${path}`,
        params: { headers: authHeaders(token), tags: { name: metric } },
        extract,
    };
}

/**
 * Builds the set of sync metrics that verify the sync completed.
 * @param {string} token
 * @returns {MetricRequest[]}
 */
export function buildMetrics(token) {
    /** @type {MetricRequest[]} */
    const metrics = [];

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

/**
 * Fires every metric request and returns the responses in order.
 * @param {MetricRequest[]} metrics
 * @returns {K6Response[]}
 */
export function executeMetrics(metrics) {
    return metrics.map((metric) => http.get(metric.url, metric.params));
}

/**
 * Polls the sync metrics once and reports whether the sync is complete.
 * @param {MetricRequest[]} metrics
 * @returns {boolean}
 */
export function pollOnce(metrics) {
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

/**
 * Waits until the sync service has published all tables.
 * @param {number} timeoutSeconds
 * @returns {boolean}
 */
export function waitForPipeline(timeoutSeconds) {
    return waitUntil(
        timeoutSeconds,
        () => pollOnce(buildMetrics(userToken())),
        "the sync service to publish every table",
    );
}

/**
 * Reads the number of rows per partition through the proxy.
 * @param {string} token
 * @param {Record<string, string>} [extra={}]
 * @returns {{ response: K6Response; counts: Map<string, number> }}
 */
export function rowsPerPartition(token, extra = {}) {
    const response = proxyGet(
        `/${PARTITIONED_TABLE}?select=${PARTITION_COLUMN},count()&order=${PARTITION_COLUMN}.asc`,
        token,
        extra,
    );

    const counts = new Map();

    rowsOf(response).forEach((row) => {
        counts.set(String(row[PARTITION_COLUMN]), Number(row.count));
    });

    return { response, counts };
}
