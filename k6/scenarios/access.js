import http from "k6/http";
import { sleep } from "k6";
import {
    API_URL,
    authHeaders,
    clientToken,
    fetchToken,
    FULL_TABLE,
    HOST,
    MULTI_RLS_TABLE,
    OIDC_NO_POLICY_CLIENT_ID,
    PARTITIONED_TABLE,
    postgrestCall,
    postgrestRows,
    proxyGet,
    rowsOf,
    safeJson,
    scenario,
    SCHEMA,
    snapshotHeader,
    sourceHeader,
    userToken,
} from "../lib/index.js";

const { expect, expectEach, checks } = scenario("access");

/**
 * Verifies that a user without a policy gets a 200 with zero rows.
 */
export function noAccess() {
    const token = fetchToken(OIDC_NO_POLICY_CLIENT_ID);
    let status = 0;

    /** @type {unknown} */
    let body = null;
    let source = "";
    let snapshot = "";

    for (let attempt = 0; attempt < 3; attempt++) {
        const response = http.get(`${API_URL}/${FULL_TABLE}?limit=1`, {
            headers: authHeaders(token),
            tags: { name: "no_access_check" },
        });

        status = response.status;
        body = safeJson(response);
        source = sourceHeader(response);
        snapshot = snapshotHeader(response);
        if (status === 200) break;
        sleep(1);
    }

    checks({
        "user without policy gets 200": () => status === 200,
        "no-access returns zero rows": () => Array.isArray(body) && body.length === 0,
        "no-access queries no source": () => source === "",
        "no-access response omits the DuckLake snapshot": () => snapshot === "",
    });

    const partitioned = proxyGet(`/${PARTITIONED_TABLE}?limit=1`, token);
    expect("no-access partitioned request answers 200", partitioned.status === 200);
    expect("no-access partitioned request returns zero rows", rowsOf(partitioned).length === 0);
    expect("no-access partitioned request queries no source", sourceHeader(partitioned) === "");
    expect("no-access partitioned response omits the DuckLake snapshot", snapshotHeader(partitioned) === "");
}

/**
 * Verifies that the schemas claim limits a data read: no role reads a schema that its token doesn't list.
 */
export function dataScope() {
    const read = /** @param {string} client @returns {Array<Record<string, unknown>> | null} */ (client) => {
        const response = proxyGet(`/${FULL_TABLE}?select=unit_id&limit=100`, fetchToken(client));

        return response.status === 200 ? rowsOf(response) : null;
    };

    const own = read("admin");
    const other = read("admin_other");
    expect("an admin reads the data of its schema", own !== null && own.length > 0);

    expect(
        "an admin reads only the permitted unit of its schema",
        (own ?? []).every((row) => row.unit_id === "unit_1"),
    );

    expect("an admin of another schema gets a 200", other !== null);

    expect(
        "an admin of another schema reads nothing, although its subject has a policy row",
        (other ?? [null]).length === 0,
    );
}

/**
 * Verifies that an authorized user cannot see rows from another unit.
 * @param {string} token
 */
export function authorizedRows(token) {
    const response = proxyGet(`/${FULL_TABLE}?select=unit_id&limit=100`, token);
    const rows = rowsOf(response);
    expect("authorized user gets a 200", response.status === 200);
    expect("authorized user receives rows", rows.length > 0);

    expect(
        "authorized user receives only permitted units",
        rows.every((row) => row.unit_id === "unit_1"),
    );

    const multi = proxyGet(`/${MULTI_RLS_TABLE}?select=region_id,group_id&limit=100`, token);
    const multiRows = rowsOf(multi);
    expect("multi-RLS table gets a 200", multi.status === 200);
    expect("multi-RLS table returns rows", multiRows.length > 0);

    expect(
        "multi-RLS rows match an authorized unit",
        multiRows.every((row) => row.region_id === "region_1" || row.group_id === "group_1"),
    );
}

/**
 * Verifies Istio rejects requests without a valid JWT.
 */
export function istioJwt() {
    const noToken = http.get(`${API_URL}/${FULL_TABLE}?limit=1`, {
        headers: { Host: HOST, "Accept-Profile": SCHEMA },
        tags: { name: "istio_no_token" },
    });

    expect("Istio rejects a request without a token", noToken.status === 403);

    const invalidToken = http.get(`${API_URL}/${FULL_TABLE}?limit=1`, {
        headers: { Host: HOST, "Accept-Profile": SCHEMA, Authorization: "Bearer invalid.jwt.token" },
        tags: { name: "istio_invalid_token" },
    });

    expect("Istio rejects a request with an invalid token", invalidToken.status === 401);
}

/**
 * Verifies that the permissions and the schemas of the token limit the data and the access policy.
 */
export function dataAccess() {
    const data = `/${FULL_TABLE}?select=unit_id&limit=1`;
    expect("a role without read_api is refused the data", proxyGet(data, clientToken("policy")).status === 403);
    expect("the anonymous client is refused the data", [401, 403].includes(proxyGet(data, clientToken("anon")).status));
    const policy = "/access_policy?select=subject&limit=1";

    expectEach(
        ["policy", "admin"],
        (client) => `the ${client} client reads the policy rows of its schema`,
        (client) => postgrestRows(SCHEMA, policy, client) > 0,
    );

    expectEach(
        ["admin_other", "api", "console"],
        (client) => `the ${client} client reads no policy row of the schema`,
        (client) => postgrestRows(SCHEMA, policy, client) === 0,
    );

    const probe = { subject: "e2e_access_probe", unit_type: "unit", unit_id: "unit_probe" };

    const remove = /** @param {string} client */ (client) =>
        rowsOf(postgrestCall(SCHEMA, "DELETE", "/access_policy?subject=eq.e2e_access_probe", client)).length;

    expectEach(
        ["admin_other", "api", "console"],
        (client) => `the ${client} client can't add a policy row`,
        (client) => postgrestCall(SCHEMA, "POST", "/access_policy", client, probe).status === 403,
    );

    expectEach(
        ["policy", "admin"],
        (client) => `the ${client} client adds and removes a policy row of its schema`,
        (client) =>
            postgrestCall(SCHEMA, "POST", "/access_policy", client, probe).status === 201 && remove(client) === 1,
    );
}

/** @type {Step[]} */
export const steps = [
    { scenario: "access", name: "noAccess", stage: 50, run: () => noAccess() },
    { scenario: "access", name: "authorizedRows", stage: 60, run: () => authorizedRows(userToken()) },
    { scenario: "access", name: "dataScope", stage: 70, run: () => dataScope() },
    { scenario: "access", name: "istioJwt", stage: 240, run: () => istioJwt() },
    { scenario: "access", name: "dataAccess", stage: 300, run: () => dataAccess() },
];
