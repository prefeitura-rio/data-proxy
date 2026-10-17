/** @import { Step } from '../lib/index.js' */
/** @import { Kubernetes } from 'k6/x/kubernetes' */
import { podMetrics, scenario } from "../lib/index.js";

const { expect } = scenario("metrics");
const POOL_METRICS = ["pgrst_db_pool_available", "pgrst_db_pool_waiting", "pgrst_db_pool_timeouts_total"];

/**
 * Verifies that PostgREST exports its pool metrics on the admin port, and that its pool isn't starved.
 * @param {Kubernetes} k8s
 */
export function postgrestMetrics(k8s) {
    const response = podMetrics(k8s, "postgrest");
    expect("a PostgREST pod answers on its admin port", response?.status === 200);
    const body = String(response?.body ?? "");

    for (const name of POOL_METRICS) {
        expect(`PostgREST exports ${name}`, body.includes(name));
    }

    const waiting = /^pgrst_db_pool_waiting\s+([0-9.]+)/m.exec(body);
    expect("no request waits for a database connection", Number(waiting?.[1] ?? NaN) === 0);
}

/** @type {Step[]} */
export const steps = [
    { scenario: "metrics", name: "postgrestMetrics", stage: 345, run: ({ k8s }) => postgrestMetrics(k8s) },
];
