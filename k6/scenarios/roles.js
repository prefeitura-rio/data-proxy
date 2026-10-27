/** @import { Kubernetes } from 'k6/x/kubernetes' */
import {
    cacheHeader,
    checkSql,
    clientToken,
    consoleCall,
    consoleRows,
    createPolicy,
    createSchema,
    FULL_SOURCE,
    FULL_TABLE,
    nothing,
    policyRow,
    postgrestCall,
    postgrestRows,
    PROBE_SCHEMA,
    PROBE_SUBJECT,
    proxyGet,
    rbacClosure,
    reloadProxy,
    removePolicy,
    removeSchema,
    rerunConfigureDb,
    rowsOf,
    runSqlJob,
    safeJson,
    scenario,
    SCHEMA,
    tokenClaims,
    waitUntil,
    withRow,
} from "../lib/index.js";

const { expect, expectEach, requirePrecondition } = scenario("roles");

/**
 * The rules of `rbac.toml`. The image build turns the file into `rbac.json`.
 * @type {RbacRules}
 */
const RBAC_RULES = JSON.parse(open("../rbac.json"));

/** The parsed `rbac.toml`. The bundle step of `scripts/k6.nu` defines it, so the matrix follows the file. */
/**
 * The test clients, one for each role. Each holds the role in its `role` claim.
 * @type {Array<[client: string, role: string]>}
 */
const CLIENTS = [
    ["api", "dp_api"],
    ["policy", "dp_policy"],
    ["console", "dp_console"],
    ["admin", "dp_admin"],
];

/**
 * The full names of the permissions: the action, then the object, as in `read_api`.
 * @type {string[]}
 */
const PERMISSIONS = Object.entries(RBAC_RULES.permissions).flatMap(([object, actions]) =>
    Object.keys(actions)
        .filter((action) => action !== "description")
        .map((action) => `${action}_${object}`),
);

const MATRIX = new Map(RBAC_RULES.roles.map((role) => [role.name, rbacClosure(RBAC_RULES, role.name)]));

const CATALOG_DOCUMENT = {
    document: {
        schemas: {
            console_bad: { source: { type: "bigquery" }, tables: [{ name: "p.d.bad", strategy: "full", n: 4 }] },
        },
    },
};

/**
 * One probe for each permission: it answers whether the client may use what the permission guards. A probe changes nothing that it doesn't clean up.
 * @type {Record<string, (client: string) => boolean>}
 */
const PROBES = {
    read_api: (client) => proxyGet(`/${FULL_TABLE}?select=unit_id&limit=1`, clientToken(client)).status === 200,
    read_ducklake: (client) =>
        proxyGet(`/rpc/dl_changes_${FULL_TABLE}?select=change_type&start_snapshot=0&limit=1`, clientToken(client))
            .status === 200,
    read_files: (client) =>
        consoleCall("GET", `/rpc/table_files?schema_name=${SCHEMA}&table_name=${FULL_SOURCE}`, client).status === 200,
    read_sync: (client) => consoleRows("/runs?limit=1", client) > 0,
    start_sync: (client) => consoleCall("POST", "/rpc/start_sync", client, { schemas: ["console_bad"] }).status !== 403,
    read_config: (client) => consoleRows(`/schemas?name=eq.${SCHEMA}`, client) > 0,
    create_config: (client) =>
        withRow(
            nothing,
            removeSchema,
            () =>
                consoleCall("POST", "/schemas", client, {
                    name: PROBE_SCHEMA,
                    source: "bigquery",
                    settings: { project: "probe" },
                }).status === 201,
        ),
    edit_config: (client) =>
        withRow(
            createSchema,
            removeSchema,
            () =>
                rowsOf(
                    consoleCall("PATCH", `/schemas?name=eq.${PROBE_SCHEMA}`, client, {
                        settings: { project: "changed" },
                    }),
                ).length === 1,
        ),
    delete_config: (client) =>
        withRow(
            createSchema,
            removeSchema,
            () => rowsOf(consoleCall("DELETE", `/schemas?name=eq.${PROBE_SCHEMA}`, client)).length === 1,
        ),
    import_config: (client) => consoleCall("POST", "/rpc/import_config", client, CATALOG_DOCUMENT).status !== 403,
    read_policy: (client) => postgrestRows(SCHEMA, "/access_policy?select=subject&limit=1", client) > 0,
    create_policy: (client) =>
        withRow(
            nothing,
            removePolicy,
            () => postgrestCall(SCHEMA, "POST", "/access_policy", client, policyRow).status === 201,
        ),
    edit_policy: (client) =>
        withRow(
            createPolicy,
            removePolicy,
            () =>
                rowsOf(
                    postgrestCall(SCHEMA, "PATCH", `/access_policy?subject=eq.${PROBE_SUBJECT}`, client, {
                        unit_id: "unit_probe_2",
                    }),
                ).length === 1,
        ),
    delete_policy: (client) =>
        withRow(
            createPolicy,
            removePolicy,
            () =>
                rowsOf(postgrestCall(SCHEMA, "DELETE", `/access_policy?subject=eq.${PROBE_SUBJECT}`, client)).length ===
                1,
        ),
};

/**
 * Verifies, for every role of `rbac.toml` and every permission, that the real token may use what the permission
 * guards exactly when the closure of the rules says so, and that each token carries its role.
 */
export function roles() {
    expect(
        "every permission of the rules file has a probe, and every probe has a permission",
        JSON.stringify([...PERMISSIONS].sort()) === JSON.stringify(Object.keys(PROBES).sort()),
    );

    expectEach(
        CLIENTS,
        ([client, role]) => `the ${client} client holds the role ${role} in its token`,
        ([client, role]) => tokenClaims(client).role === role,
    );

    expect(
        "the anonymous client holds nothing and the console refuses it",
        [401, 403].includes(consoleCall("GET", "/rpc/permissions", "anon").status),
    );

    for (const [client, role] of CLIENTS) {
        const held = MATRIX.get(role) ?? new Set();

        expect(
            `${role} lists exactly its permissions`,
            JSON.stringify(safeJson(consoleCall("GET", "/rpc/permissions", client))) ===
                JSON.stringify([...held].sort()),
        );

        for (const permission of PERMISSIONS) {
            const may = held.has(permission);
            expect(`${role} ${may ? "may" : "may not"} ${permission}`, PROBES[permission]?.(client) === may);
        }
    }
}

/**
 * Verifies the roles that the console refuses: a role without read_sync reads no row of any console view.
 */
export function consoleAccess() {
    for (const view of ["errors", "logs", "overview", "queues", "runs", "schedules", "steps", "synced", "tasks"]) {
        for (const [client, role] of CLIENTS) {
            if (MATRIX.get(role)?.has("read_sync")) continue;
            expect(`${role} reads no row of ${view}`, consoleRows(`/${view}?limit=1`, client) === 0);
        }
    }
}

/**
 * Verifies, in the cluster, that the roles of the rules file are in the database, and that no object of the API lacks a rule.
 * @param {Kubernetes} k8s
 */
export function accessCatalog(k8s) {
    const stored = RBAC_RULES.roles
        .map((role) => `${role.name}=${[...(MATRIX.get(role.name) ?? [])].sort().join(",")}`)
        .sort()
        .join(";");

    /** @type {Array<[string, string, string]>} */
    const queries = [
        [
            "access-rules",
            "SELECT string_agg(name || '=' || array_to_string(permissions, ','), ';' ORDER BY name) FROM rbac.roles",
            stored,
        ],
        [
            "access-relations",
            "SELECT string_agg(c.relname, ',' ORDER BY c.relname) FROM pg_class AS c INNER JOIN pg_namespace AS n ON c.relnamespace = n.oid WHERE n.nspname = 'console' AND c.relkind IN ('r', 'v')",
            "errors,health,logs,overview,queues,rls,runs,schedules,schemas,steps,synced,tables,tasks",
        ],
        [
            "access-functions",
            "SELECT string_agg(DISTINCT p.proname, ',' ORDER BY p.proname) FROM pg_proc AS p INNER JOIN pg_namespace AS n ON p.pronamespace = n.oid WHERE n.nspname = 'console' AND p.prorettype <> 'trigger'::regtype",
            "config,import_config,mine_table,permissions,start_scope,start_sync,sync_config,table_files,username",
        ],
        [
            "access-rbac-functions",
            "SELECT string_agg(DISTINCT p.proname, ',' ORDER BY p.proname) FROM pg_proc AS p INNER JOIN pg_namespace AS n ON p.pronamespace = n.oid WHERE n.nspname = 'rbac'",
            "can,load,mine,unknown",
        ],
        ["access-no-old-schemas", "SELECT to_regnamespace('admin') IS NULL AND to_regnamespace('authz') IS NULL", "t"],
        [
            "access-closed-objects",
            "SELECT has_function_privilege('dp_admin', 'data_proxy.reload_postgrest()', 'EXECUTE') OR has_function_privilege('anon', 'data_proxy.wait_for_standbys(interval)', 'EXECUTE') OR has_table_privilege('dp_admin', 'dbos.workflow_status', 'SELECT') OR has_table_privilege('dp_admin', 'data_proxy.state', 'SELECT')",
            "f",
        ],
        [
            "access-helper-functions",
            "SELECT coalesce(string_agg(p.proname, ','), '') FROM pg_proc AS p INNER JOIN pg_namespace AS n ON p.pronamespace = n.oid WHERE n.nspname = 'test' AND (p.proname ~ '_(dl|bq)_fn$' OR p.proname = 'dl_table_files') AND has_function_privilege('dp_api', p.oid, 'EXECUTE')",
            "",
        ],
        [
            "access-table-functions",
            "SELECT count(*) FROM pg_proc AS p INNER JOIN pg_namespace AS n ON p.pronamespace = n.oid WHERE n.nspname = 'test' AND p.proname ~ '_fn$' AND p.proname !~ '_(dl|bq)_fn$' AND NOT has_function_privilege('dp_api', p.oid, 'EXECUTE')",
            "0",
        ],
        [
            "access-proxy-reload-chain",
            "SELECT count(*) > 0 FROM dbos.workflow_status WHERE name = 'reload_proxy' AND status = 'SUCCESS'",
            "t",
        ],
        [
            "access-anon-functions",
            "SELECT string_agg(p.proname, ',') FROM pg_proc AS p INNER JOIN pg_namespace AS n ON p.pronamespace = n.oid WHERE n.nspname = 'console' AND has_function_privilege('anon', p.oid, 'EXECUTE') AND p.prorettype <> 'trigger'::regtype",
            "config",
        ],
    ];

    queries.forEach(([label, query, expected]) => checkSql(k8s, label, query, expected));
}

/**
 * The clients whose token carries a role that no token may use: other database roles, an unknown role, and no role.
 * @type {string[]}
 */
const ESCALATION_CLIENTS = ["role_ops", "role_serving", "role_postgres", "role_ghost", "role_none"];
const REFUSED = [400, 401, 403];

/**
 * Verifies that a token can't become a database role that isn't a request role, and that a token without a role gets none.
 */
export function escalation() {
    const data = `/${FULL_TABLE}?select=unit_id&limit=1`;

    expectEach(
        ESCALATION_CLIENTS,
        (client) => `a token of the client ${client} is refused the data`,
        (client) => REFUSED.includes(proxyGet(data, clientToken(client)).status),
    );

    expectEach(
        ESCALATION_CLIENTS,
        (client) => `a token of the client ${client} reads nothing from the console`,
        (client) => consoleRows("/overview", client) <= 0,
    );
}

/**
 * Verifies that a `schemas` claim that is a plain text works like a list, and that an empty list reads nothing.
 */
export function claimShapes() {
    const path = `/${FULL_TABLE}?select=unit_id&limit=5`;
    const text = proxyGet(path, clientToken("schemas_text"));
    expect("a schemas claim that is a plain text is served", text.status === 200);
    expect("a schemas claim that is a plain text reads the rows of its schema", rowsOf(text).length > 0);
    const empty = proxyGet(path, clientToken("schemas_empty"));
    expect("a token with an empty schemas claim is served", empty.status === 200);
    expect("a token with an empty schemas claim reads no row", rowsOf(empty).length === 0);
}

const UNKNOWN_FUNCTION = "e2e_unknown_permission";

/**
 * Verifies that a function that asks for a permission that the rules don't list ends as an HTTP 500 with the reason.
 * @param {Kubernetes} k8s
 */
export function unknownPermission(k8s) {
    const reload = "NOTIFY pgrst, 'reload schema'";

    try {
        runSqlJob(
            k8s,
            "create-unknown-permission-function",
            `CREATE OR REPLACE FUNCTION ${SCHEMA}.${UNKNOWN_FUNCTION}() RETURNS boolean LANGUAGE sql STABLE AS 'SELECT rbac.can(''e2e_unknown'')'; GRANT EXECUTE ON FUNCTION ${SCHEMA}.${UNKNOWN_FUNCTION}() TO dp_api; ${reload}`,
        );

        let response = proxyGet(`/rpc/${UNKNOWN_FUNCTION}`, clientToken("api"));

        waitUntil(
            60,
            () => {
                response = proxyGet(`/rpc/${UNKNOWN_FUNCTION}`, clientToken("api"));

                return response.status !== 404;
            },
            "PostgREST to list the test function",
        );

        expect("an unknown permission ends as an HTTP 500", response.status === 500);
        expect("the answer names the unknown permission", String(response.body).includes("Unknown permission"));
    } finally {
        runSqlJob(
            k8s,
            "drop-unknown-permission-function",
            `DROP FUNCTION IF EXISTS ${SCHEMA}.${UNKNOWN_FUNCTION}(); ${reload}`,
        );
    }
}

/**
 * Verifies that a rerun of configure-db with other rules changes what a role may do at once, also for an answer
 * that the proxy cached, and that the original rules bring the access back.
 * @param {Kubernetes} k8s
 */
export function rulesReload(k8s) {
    const path = `/${FULL_TABLE}?select=id&limit=3`;
    const token = clientToken("api");
    const withoutReadApi = `sed 's/^permissions = \\["read_api"\\]$/permissions = []/' /scripts/rbac.toml`;
    proxyGet(path, token);
    requirePrecondition("the answer is cached", cacheHeader(proxyGet(path, token)) === "HIT", {});

    try {
        rerunConfigureDb(k8s, "rules-without-read-api", withoutReadApi);
        expect("the proxy reloads its settings after the new rules", reloadProxy().status === 200);
        const refused = proxyGet(path, token);
        expect("a role that lost read_api is refused", refused.status === 403);
        expect("a role that lost read_api reads no cached answer", cacheHeader(refused) !== "HIT");
    } finally {
        rerunConfigureDb(k8s, "rules-restored");
        reloadProxy();
    }

    const restored = proxyGet(path, token);
    expect("the original rules give the role its access back", restored.status === 200);
    expect("the original rules give the role its rows back", rowsOf(restored).length > 0);
}

/** @type {Step[]} */
export const steps = [
    { scenario: "roles", name: "roles", stage: 280, run: () => roles() },
    { scenario: "roles", name: "escalation", stage: 285, run: () => escalation() },
    { scenario: "roles", name: "claimShapes", stage: 286, run: () => claimShapes() },
    { scenario: "roles", name: "unknownPermission", stage: 287, run: ({ k8s }) => unknownPermission(k8s) },
    { scenario: "roles", name: "rulesReload", stage: 288, run: ({ k8s }) => rulesReload(k8s) },
    { scenario: "roles", name: "consoleAccess", stage: 290, run: () => consoleAccess() },
    { scenario: "roles", name: "accessCatalog", stage: 320, run: ({ k8s }) => accessCatalog(k8s) },
];
