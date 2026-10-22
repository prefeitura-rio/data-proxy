/** @import { Kubernetes } from 'k6/x/kubernetes' */
import { sleep } from "k6";
import {
    cacheHeader,
    FULL_TABLE,
    info,
    killPod,
    logHas,
    NAMESPACE,
    proxyGet,
    scenario,
    userToken,
    VALKEY_POD,
    waitUntil,
} from "../lib/index.js";

const { expect, requirePrecondition } = scenario("cachestore");
const SAMPLES = 8;
const SAMPLE_INTERVAL_SECONDS = 2;
const RETURN_TIMEOUT_SECONDS = 180;

/**
 * Reports whether the Valkey pod exists and is ready.
 * @param {Kubernetes} k8s
 * @returns {boolean}
 */
function valkeyReady(k8s) {
    const pod = k8s.list("Pod", NAMESPACE).find((item) => item.metadata.name === VALKEY_POD);

    return pod?.status?.conditions?.some((c) => c.type === "Ready" && c.status === "True") === true;
}

/**
 * Verifies that the proxy serves reads while Valkey is down, warns in its log, and caches again afterwards.
 * @param {Kubernetes} k8s
 */
export function valkeyOutage(k8s) {
    const path = `/${FULL_TABLE}?select=id&limit=3`;
    const token = userToken();
    proxyGet(path, token);

    requirePrecondition(
        "the proxy caches the answer before the outage",
        cacheHeader(proxyGet(path, token)) === "HIT",
        {},
    );

    info("kill_pod", { pod: VALKEY_POD });
    killPod(k8s, VALKEY_POD);

    /** @type {number[]} */
    const statuses = [];

    /** @type {boolean[]} */
    const uncached = [];

    for (let sample = 0; sample < SAMPLES; sample++) {
        const response = proxyGet(path, token);
        statuses.push(response.status);
        uncached.push(cacheHeader(response) !== "HIT");
        sleep(SAMPLE_INTERVAL_SECONDS);
    }

    expect(
        "every read is served while Valkey is down",
        statuses.every((status) => status === 200),
    );

    expect(
        "no read is a cache hit while Valkey is down",
        uncached.some((value) => value),
    );

    expect(
        "the proxy logs the Redis warning",
        logHas(k8s, "check-redis-warning", "app.kubernetes.io/component=proxy", "Redis cache"),
    );

    expect(
        "Valkey comes back",
        waitUntil(RETURN_TIMEOUT_SECONDS, () => valkeyReady(k8s), "Valkey to be ready"),
    );

    expect(
        "the proxy caches again after Valkey returns",
        waitUntil(
            60,
            () => {
                proxyGet(path, token);

                return cacheHeader(proxyGet(path, token)) === "HIT";
            },
            "the proxy to cache again",
        ),
    );
}

/** @type {Step[]} */
export const steps = [
    { scenario: "cachestore", name: "valkeyOutage", stage: 890, run: ({ k8s }) => valkeyOutage(k8s) },
];
