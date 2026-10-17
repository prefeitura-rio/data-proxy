/** @import { Step } from '../lib/index.js' */
import http from "k6/http";
import { sleep } from "k6";
import {
    API_URL,
    authHeaders,
    cacheHeader,
    changeAccessPolicy,
    createPolicy,
    fetchToken,
    FULL_TABLE,
    MULTI_RLS_TABLE,
    OIDC_NO_POLICY_CLIENT_ID,
    PARTITIONED_TABLE,
    proxyGet,
    reloadProxy,
    removePolicy,
    rowsOf,
    scenario,
    sourceHeader,
    stable,
} from "../lib/index.js";

const { expect, requirePrecondition } = scenario("cache");
const CACHE_TTL_SECONDS = Number(__ENV.CACHE_TTL_SECONDS || "5");
const BURST_REQUESTS = 5;

/**
 * Verifies cache hits, token refresh, forged token refusal, and TTL expiry.
 * @param {string} token
 * @param {string} otherToken
 */
function verifyProxyCache(token, otherToken) {
    const path = `/${FULL_TABLE}?select=id&limit=4`;
    const first = proxyGet(path, token);

    requirePrecondition("the first answer is a cache miss", cacheHeader(first) === "MISS", {
        cache: cacheHeader(first),
    });

    requirePrecondition("the first answer has rows", rowsOf(first).length > 0, { status: first.status });
    const second = proxyGet(path, token);
    expect("a repeated request is served from the cache", cacheHeader(second) === "HIT");
    const refreshed = fetchToken();
    requirePrecondition("a fresh token differs from the first one", refreshed !== token, {});
    const rotated = proxyGet(path, refreshed);
    expect("a refreshed token is served from the cache", cacheHeader(rotated) === "HIT");
    expect("a refreshed token receives the same answer", stable(rowsOf(rotated)) === stable(rowsOf(second)));
    const forged = proxyGet(path, "forged.token.value");
    expect("a forged token is refused", forged.status === 401 || forged.status === 403);
    expect("a forged token is not served the cached answer", rowsOf(forged).length === 0);
    expect("the cached answer is the same", stable(rowsOf(second)) === stable(rowsOf(first)));
    const otherSubject = proxyGet(path, otherToken);
    expect("another subject does not receive the answer", stable(rowsOf(otherSubject)) !== stable(rowsOf(first)));

    expect(
        "a cache hit keeps the source of the stored answer",
        sourceHeader(proxyGet(path, token)) === sourceHeader(first),
    );

    const probe = [{ subject: "e2e_cache_probe", unit_type: "unit", unit_id: "unit_cache" }];
    expect("a new policy row is stored", changeAccessPolicy("POST", "", probe) === 201);
    expect("a change of the access policy makes a cached answer stale", cacheHeader(proxyGet(path, token)) === "MISS");
    expect("the new policy row is removed again", changeAccessPolicy("DELETE", "?subject=eq.e2e_cache_probe") < 300);
    sleep(CACHE_TTL_SECONDS + 3);
    expect("an entry expires after the configured lifetime", cacheHeader(proxyGet(path, token)) === "MISS");
}

/**
 * Verifies that a per-table cache TTL outlives the global one.
 * @param {string} token
 */
function verifyProxyLifetimes(token) {
    const longPath = `/${PARTITIONED_TABLE}?select=id&limit=2`;
    const shortPath = `/${FULL_TABLE}?select=id&limit=2`;

    requirePrecondition(
        "the table with its own lifetime answers",
        cacheHeader(proxyGet(longPath, token)) === "MISS",
        {},
    );

    requirePrecondition(
        "the table with the global lifetime answers",
        cacheHeader(proxyGet(shortPath, token)) === "MISS",
        {},
    );

    sleep(CACHE_TTL_SECONDS + 3);
    expect("a table lifetime outlives the global one", cacheHeader(proxyGet(longPath, token)) === "HIT");
    expect("the global lifetime still applies", cacheHeader(proxyGet(shortPath, token)) === "MISS");
}

/**
 * Verifies that a concurrent burst shares one upstream call.
 * @param {string} token
 */
function verifyCoalescing(token) {
    const path = `/${MULTI_RLS_TABLE}?select=id&limit=5`;

    /** @type {{ method: string; url: string; params: { headers: Record<string, string>; tags: Record<string, string> }; }[]} */
    const burst = [];

    for (let i = 0; i < BURST_REQUESTS; i++) {
        burst.push({
            method: "GET",
            url: `${API_URL}${path}`,
            params: { headers: authHeaders(token), tags: { name: "coalesce" } },
        });
    }

    requirePrecondition("the burst path is not cached yet", cacheHeader(proxyGet(path, token)) === "MISS", {});
    proxyGet("/e2e", token);
    const responses = http.batch(burst);
    const bodies = responses.filter((r) => rowsOf(r).length > 0);
    expect("a burst is answered with rows", bodies.length === responses.length);

    expect(
        "a burst is served the same answer",
        stable(rowsOf(responses[0])) === stable(rowsOf(responses[BURST_REQUESTS - 1])),
    );
}

/**
 * Verifies the proxy cache, its lifetimes, and request coalescing.
 */
export function proxy() {
    const token = fetchToken();
    const noAccessToken = fetchToken(OIDC_NO_POLICY_CLIENT_ID);
    verifyProxyCache(token, noAccessToken);
    verifyProxyLifetimes(token);
    verifyCoalescing(token);
}

/**
 * Verifies that a write to the access policy that skips the proxy makes the cached answers stale. The console
 * and `configure-db` change the policy and the rules without the proxy. The revision in the settings tells it.
 */
export function policyInvalidation() {
    const token = fetchToken();
    const path = `/${FULL_TABLE}?select=id&limit=3`;
    proxyGet(path, token);
    requirePrecondition("the answer is cached", cacheHeader(proxyGet(path, token)) === "HIT", {});

    try {
        requirePrecondition("the policy write skips the proxy", createPolicy().status === 201, {});
        const reload = reloadProxy();
        expect("the proxy reloads its settings", reload.status === 200);

        expect(
            "a policy write that skipped the proxy makes a cached answer stale",
            cacheHeader(proxyGet(path, token)) === "MISS",
        );
    } finally {
        removePolicy();
        reloadProxy();
    }
}

/** @type {Step[]} */
export const steps = [
    { scenario: "cache", name: "proxy", stage: 250, run: () => proxy() },
    { scenario: "cache", name: "policyInvalidation", stage: 252, run: () => policyInvalidation() },
];
