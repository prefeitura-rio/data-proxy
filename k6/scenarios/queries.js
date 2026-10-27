/** @import { Kubernetes } from 'k6/x/kubernetes' */
import http from "k6/http";
import { sleep } from "k6";
import {
    API_URL,
    authHeaders,
    cacheHeader,
    clearProxyCache,
    directPostgrest,
    fetchToken,
    FULL_TABLE,
    proxyGet,
    rowsOf,
    safeJson,
    scenario,
    snapshotHeader,
    stable,
    userToken,
    warn,
} from "../lib/index.js";

const { expect, requirePrecondition, checks } = scenario("queries");

/**
 * Verifies that jsonb columns and json path filters work through the proxy.
 */
export function jsonb() {
    const token = fetchToken();
    let filterStatus = 0;

    /** @type {unknown} */
    let filterBody = null;

    for (let attempt = 0; attempt < 3; attempt++) {
        const response = http.get(`${API_URL}/${FULL_TABLE}?metadata->>status=not.is.null&limit=1`, {
            headers: authHeaders(token),
            tags: { name: "json_path_filter" },
        });

        filterStatus = response.status;
        filterBody = safeJson(response);
        if (filterStatus === 200) break;
        sleep(1);
    }

    checks({
        "json path filter returns 200": () => filterStatus === 200,
        "json path filter returns rows": () => Array.isArray(filterBody) && filterBody.length > 0,
    });
}

/**
 * Verifies supported filters produce the same rows through the proxy and PostgREST.
 * @param {string} token
 * @param {Kubernetes} k8s
 */
export function filterPushdown(token, k8s) {
    clearProxyCache(k8s);
    const baseline = directPostgrest(`/${FULL_TABLE}?select=id&order=id.asc&limit=3`, token);
    const baselineRows = rowsOf(baseline);
    const snapshot = snapshotHeader(baseline);

    /** @type {Record<string, string>} */
    const snapshotHeaders = {
        "X-DuckLake-Snapshot": snapshot,
    };

    const ids = baselineRows
        .map((row) => row.id)
        .filter(/** @returns {id is string | number} */ (id) => typeof id === "string" || typeof id === "number")
        .map(String);

    const detail = { status: baseline.status, ids, snapshot };
    requirePrecondition("the direct baseline request answers 200", baseline.status === 200, detail);
    requirePrecondition("filter pushdown has three authorized IDs", ids.length === 3, detail);
    requirePrecondition("the baseline names the snapshot that it read", /^[0-9]+$/.test(snapshot), detail);

    const cases = [
        {
            name: "scalar filter preserves projection and pagination",
            query: `id=eq.${ids[0]}&select=id,name,unit_id&order=id.asc&limit=1&offset=0`,
        },
        {
            name: "in filter preserves ordering and pagination",
            query: `id=in.(${ids.join(",")})&select=id,name&order=id.desc&limit=2&offset=1`,
        },
        {
            name: "repeated filters are combined",
            query: `id=eq.${ids[0]}&id=eq.${ids[0]}&select=id,name&order=id.asc`,
        },
        {
            name: "nested logical filters are preserved",
            query: `and=(or(id.eq.${ids[0]},id.eq.${ids[1]}),not.and(id.eq.${ids[0]},id.eq.${ids[0]}))&select=id,name,unit_id&order=id.asc`,
        },
        {
            name: "an order column that the request does not select still orders the page",
            query: `select=name&order=id.desc&limit=2`,
        },
        {
            name: "a count asks for every row, so the limit stays in PostgREST",
            query: `select=id,name&order=id.asc&limit=2`,
            headers: { Prefer: "count=exact" },
        },
        {
            name: "select star reads every column of the page",
            query: `select=*&order=id.asc&limit=2`,
        },
    ];

    cases.forEach(({ name, query, headers }) => {
        const caseHeaders = { ...snapshotHeaders, ...headers };
        const direct = directPostgrest(`/${FULL_TABLE}?${query}`, token, caseHeaders);
        const proxy = proxyGet(`/${FULL_TABLE}?${query}`, token, caseHeaders);
        expect(`${name}: direct request succeeds`, direct.status === 200);
        expect(`${name}: proxy request succeeds`, proxy.status === 200);
        expect(`${name}: proxy request is not served from cache`, cacheHeader(proxy) === "MISS");
        const directRows = rowsOf(direct);
        const proxyRows = rowsOf(proxy);
        const rowsMatch = stable(proxyRows) === stable(directRows);

        if (!rowsMatch) {
            warn("rows_differ", {
                name,
                query,
                snapshot,
                direct: stable(directRows).slice(0, 300),
                proxy: stable(proxyRows).slice(0, 300),
            });
        }

        expect(`${name}: proxy rows match direct rows`, rowsMatch);
    });
}

/** @type {Step[]} */
export const steps = [
    { scenario: "queries", name: "filterPushdown", stage: 10, run: ({ k8s }) => filterPushdown(userToken(), k8s) },
    { scenario: "queries", name: "jsonb", stage: 80, run: () => jsonb() },
];
