import http from "k6/http";
import { sleep } from "k6";
import {
    API_URL,
    FULL_TABLE,
    HOST,
    POLICY_CLIENT_ID,
    POLICY_REPLICATION_POLL_INTERVAL_SECONDS,
    POLICY_REPLICATION_TIMEOUT_SECONDS,
    SCHEMA,
} from "./env.js";
import { proxyGet, rowsOf } from "./http.js";
import { fetchToken } from "./auth.js";
import { scenario } from "./check.js";
import { rememberRequest } from "./log.js";

const setup = scenario("setup");

export const ACCESS_POLICY_ROWS = [
    {
        subject: "test_user_1",
        unit_type: "unit",
        unit_id: "unit_1",
    },
    {
        subject: "test_user_1",
        unit_type: "region",
        unit_id: "region_1",
    },
    {
        subject: "test_user_1",
        unit_type: "group",
        unit_id: "group_1",
    },
    // The roles that read the table with a schema claim: one in the claim and one outside it.
    { subject: "test_admin", unit_type: "unit", unit_id: "unit_1" },
    { subject: "test_admin", unit_type: "region", unit_id: "region_1" },
    { subject: "test_admin", unit_type: "group", unit_id: "group_1" },
    { subject: "test_admin_other", unit_type: "unit", unit_id: "unit_1" },
];

/**
 * Seeds the access policy table so RLS grants the test users their units. A row that exists answers 409, which isn't a failure.
 * @param {readonly object[]} [rows=ACCESS_POLICY_ROWS]
 */
export function seedAccessPolicy(rows = ACCESS_POLICY_ROWS) {
    const token = fetchToken(POLICY_CLIENT_ID);

    const headers = {
        Authorization: `Bearer ${token}`,
        Host: HOST,
        "Accept-Profile": SCHEMA,
        "Content-Profile": SCHEMA,
        "Content-Type": "application/json",
        Prefer: "resolution=merge-duplicates",
    };

    let status = 0;

    for (let attempt = 0; attempt < 3; attempt++) {
        const response = http.post(`${API_URL}/access_policy`, JSON.stringify(rows), {
            headers,
            tags: { name: "seed_access_policy" },
            responseCallback: http.expectedStatuses(201, 409),
        });

        status = response.status;
        if (status === 201 || status === 409) break;
        sleep(1);
    }

    setup.checks({
        "access_policy seeded": () => status === 201 || status === 409,
    });
}

/**
 * Waits until the seeded policy authorizes the user through the read path.
 * @param {string} token
 */
export function waitForAccessPolicyReplication(token) {
    const deadline = Date.now() + POLICY_REPLICATION_TIMEOUT_SECONDS * 1000;
    const path = `/${FULL_TABLE}?limit=1`;

    while (Date.now() < deadline) {
        const response = proxyGet(path, token);
        const rows = rowsOf(response);

        if (response.status === 200 && rows.length > 0) {
            return;
        }

        sleep(POLICY_REPLICATION_POLL_INTERVAL_SECONDS);
    }

    throw new Error("access_policy did not authorize the read replica in time");
}

/**
 * Changes the access policy through the proxy with the policy writer, and returns the status.
 * @param {"POST" | "DELETE"} method
 * @param {string} query
 * @param {unknown} [rows]
 * @returns {number}
 */
export function changeAccessPolicy(method, query, rows) {
    const params = {
        headers: {
            Authorization: `Bearer ${fetchToken(POLICY_CLIENT_ID)}`,
            Host: HOST,
            "Accept-Profile": SCHEMA,
            "Content-Profile": SCHEMA,
            "Content-Type": "application/json",
        },
        tags: { name: "change_access_policy" },
    };

    const url = `${API_URL}/access_policy${query}`;
    const response = method === "POST" ? http.post(url, JSON.stringify(rows), params) : http.del(url, null, params);

    return rememberRequest(response).status;
}
