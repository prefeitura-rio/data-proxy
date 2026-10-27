import http from "k6/http";
import {
    CONSOLE_VIEWS,
    consoleCall,
    consoleRows,
    documentBecomes,
    documentOf,
    FAILING_TABLE,
    FULL_SOURCE,
    OIDC_USER_CLIENT_ID,
    PARTITIONED_TABLE,
    POSTGREST_URL,
    rowsOf,
    safeJson,
    scenario,
    SCHEMA,
    SYNCED_PARTITIONS,
    TABLES,
    tokenClaims,
    waitUntil,
} from "../lib/index.js";

const { expect, expectEach } = scenario("console");

/**
 * Verifies the API of the console after the sync: reads are scoped to the schemas of the token,
 * each level changes only what it may, and the files of a table are listed for the query tab.
 * The clients are in the k3d Keycloak realm.
 */
export function console() {
    const overview = rowsOf(consoleCall("GET", "/overview", "console"));
    expect("a viewer reads one overview row", overview.length === 1);
    expect("the overview row is the one of its schema", overview[0]?.name === SCHEMA);
    expect("the overview counts every fixture table", Number(overview[0]?.tables) >= TABLES.length);
    expect("a viewer sees the runs that covered its schema", consoleRows("/runs", "console") > 0);
    expect("a viewer sees the tasks that covered its schema", consoleRows("/tasks", "console") > 0);

    expectEach(
        ["/schemas", "/tables", "/overview", "/runs", "/tasks", "/logs?limit=5"],
        (path) => `a token for another schema reads nothing from ${path.split("?")[0]}`,
        (path) => consoleRows(path, "admin_other") === 0,
    );

    const claim = tokenClaims("admin").schemas;

    const seen = rowsOf(consoleCall("GET", "/schemas?select=name", "admin")).map((row) => String(row.name));

    expect("the admin sees its own schema", seen.includes(SCHEMA));

    expect(
        "the admin sees only the schemas of its claim",
        Array.isArray(claim) && seen.every((name) => claim.includes(name)),
    );

    expectEach(
        ["api", "console", "admin"],
        (client) => `the ${client} client can't change a queue`,
        (client) => consoleCall("PATCH", "/queues?name=eq.sync", client, { polling_interval_sec: 1 }).status === 403,
    );

    expect(
        "an admin can't start a run for a schema outside its token",
        consoleCall("POST", "/rpc/start_sync", "admin", { schemas: ["other"] }).status === 403,
    );

    expect("a console role can't start a run", consoleCall("POST", "/rpc/start_sync", "console", {}).status === 403);
    const table = `/tables?name=eq.${FULL_SOURCE}`;

    expect(
        "a console role can't change the cache lifetime of a table",
        rowsOf(consoleCall("PATCH", table, "console", { cache_ttl: 7 })).length === 0,
    );

    expect(
        "an admin of another schema can't change the cache lifetime of a table",
        rowsOf(consoleCall("PATCH", table, "admin_other", { cache_ttl: 7 })).length === 0,
    );

    expect(
        "an admin changes the cache lifetime of its table",
        rowsOf(consoleCall("PATCH", table, "admin", { cache_ttl: 7 })).length === 1,
    );

    expect(
        "the admin puts the cache lifetime back",
        rowsOf(consoleCall("PATCH", table, "admin", { cache_ttl: null })).length === 1,
    );

    const filesPath = `/rpc/table_files?schema_name=${SCHEMA}&table_name=${FULL_SOURCE}`;
    const files = consoleCall("GET", filesPath, "admin");
    const listed = rowsOf(files);
    expect("an admin lists the files of a table of its schema", files.status === 200 && listed.length > 0);

    expect(
        "the files are Parquet files",
        listed.every((file) => String(file.path).endsWith(".parquet")),
    );

    expectEach(
        ["console", "admin_other"],
        (client) => `the ${client} client can't list the files of the table`,
        (client) => consoleCall("GET", filesPath, client).status === 403,
    );
}

const CONSOLE_FUNCTIONS = ["config", "sync_config", "username"];
const CONSOLE_LEVELS = ["console", "admin"];

/**
 * Verifies the rest of the console API: every view and helper answers at every level, the
 * status views describe the synced schema, the admin writes the configuration, queues and
 * schedules are read-only, and a request without the right token is refused.
 */
export function surface() {
    CONSOLE_LEVELS.forEach((client) => {
        CONSOLE_VIEWS.forEach((view) =>
            expect(`${client} reads ${view}`, consoleCall("GET", `/${view}?limit=1`, client).status === 200),
        );

        CONSOLE_FUNCTIONS.forEach((name) =>
            expect(`${client} calls ${name}`, consoleCall("GET", `/rpc/${name}`, client).status === 200),
        );
    });

    expect(
        "the helper function returns the user name of the token",
        safeJson(consoleCall("GET", "/rpc/username", "console")) === "test_console",
    );

    const health = rowsOf(consoleCall("GET", `/health?name=eq.${SCHEMA}`, "console"));
    expect("health has one row for the provisioned schema", health.length === 1);
    expect("health shows that the schema exists", health[0]?.schema_exists === true);
    expect("health shows that the access policy exists", health[0]?.access_policy === true);
    expect("health counts the views of the schema", Number(health[0]?.view_count) > 0);
    const synced = rowsOf(consoleCall("GET", `/synced?schema=eq.${SCHEMA}`, "console"));
    const row = /** @param {string} name */ (name) => synced.find((item) => String(item.name).endsWith(`.${name}`));

    expectEach(
        TABLES,
        (name) => `synced lists ${name} as synced`,
        (name) => row(name)?.synced === true,
    );

    expect(
        "synced counts the partitions of the partitioned table",
        Number(row(PARTITIONED_TABLE)?.partition_count) >= SYNCED_PARTITIONS,
    );

    expect("synced shows the blocked table as not synced", row(FAILING_TABLE)?.synced === false);
    expect("a viewer reads the logs of its runs", consoleRows(`/logs?schema_name=eq.${SCHEMA}&limit=1`, "console") > 0);
    expect("a viewer reads the steps of its runs", consoleRows("/steps?limit=1", "console") > 0);
    expect("the admin reads the queue of the sync", consoleRows("/queues?name=eq.sync", "admin") === 1);
    const schedule = rowsOf(consoleCall("GET", "/schedules?limit=1", "admin"))[0];

    if (schedule !== undefined) {
        expectEach(
            ["api", "console", "admin"],
            (client) => `the ${client} client can't change a schedule`,
            (client) =>
                consoleCall("PATCH", `/schedules?name=eq.${schedule.name}`, client, {
                    timezone: schedule.timezone,
                }).status === 403,
        );
    }

    const probe = { name: "console_probe", source: "bigquery", settings: { project: "probe" } };
    const probeCount = () => consoleRows("/schemas?name=eq.console_probe", "admin");

    expectEach(
        ["console", "admin_other"],
        (client) => `the ${client} client can't add a schema`,
        (client) => consoleCall("POST", "/schemas", client, probe).status === 403,
    );

    expect("the admin adds a schema", consoleCall("POST", "/schemas", "admin", probe).status === 201);

    expect(
        "a standby shows the new schema",
        waitUntil(10, () => probeCount() === 1, "a standby to show the new schema"),
    );

    expectEach(
        ["console", "admin_other"],
        (client) => `the ${client} client deletes no schema`,
        (client) => rowsOf(consoleCall("DELETE", "/schemas?name=eq.console_probe", client)).length === 0,
    );

    expect("the schema is still there after the refused deletes", probeCount() === 1);

    expect(
        "the admin deletes the schema",
        rowsOf(consoleCall("DELETE", "/schemas?name=eq.console_probe", "admin")).length === 1,
    );

    expect(
        "a standby shows that the schema is gone",
        waitUntil(10, () => probeCount() === 0, "a standby to drop the schema"),
    );

    expect(
        "a console role can't import a configuration",
        consoleCall("POST", "/rpc/import_config", "console", { document: { schemas: {} } }).status === 403,
    );

    expectEach(
        ["console", "admin_other"],
        (client) => `the ${client} client deletes no table`,
        (client) => rowsOf(consoleCall("DELETE", `/tables?name=eq.${FULL_SOURCE}`, client)).length === 0,
    );

    const anonymous = http.get(`${POSTGREST_URL}/overview`, {
        headers: { "Accept-Profile": "console" },
        tags: { name: "console:anonymous" },
    });

    expect("a request without a token is refused", [401, 403].includes(anonymous.status));
    expect("a token of a data user sees nothing", consoleRows("/overview", OIDC_USER_CLIENT_ID) === 0);
}

/**
 * Verifies the HTTP contract of the console with an admin: rows in, a document out, and errors as statuses.
 */
export function behaviour() {
    const alpha = "console_alpha";
    const beta = "console_beta";

    const created = [
        consoleCall("POST", "/schemas", "admin", { name: alpha, claim: "sub" }),
        consoleCall("POST", "/schemas", "admin", { name: beta }),
        consoleCall("POST", "/tables", "admin", {
            name: "p.d.one",
            schema: alpha,
            strategy: "partitioned",
            n: 4,
        }),
        consoleCall("POST", "/tables", "admin", { name: "p.d.beta", schema: beta }),
        consoleCall("POST", "/rls", "admin", {
            table_name: "p.d.one",
            column_name: "unit_id",
            unit_type: "unit",
        }),
    ];

    ["schema alpha", "schema beta", "table p.d.one", "table p.d.beta", "the rule of p.d.one"].forEach((what, index) =>
        expect(`${what} goes in as a row with a 201`, created[index]?.status === 201),
    );

    expect(
        "sync.json shows a schema, a table, and a rule that went in as rows",
        documentBecomes(alpha, {
            source: { type: "bigquery" },
            claim: "sub",
            tables: [
                {
                    name: "p.d.one",
                    strategy: "partitioned",
                    n: 4,
                    rls: [{ column: "unit_id", unit_type: "unit" }],
                },
            ],
        }),
    );

    const row = rowsOf(consoleCall("GET", `/schemas?name=eq.${alpha}`, "admin"))[0];
    expect("every row records who made it, from the token", row?.updated_by === "test_admin");

    const statuses = [
        consoleCall("POST", "/schemas", "admin", { name: "Bad-Name" }).status,
        consoleCall("POST", "/schemas", "admin", { name: alpha }).status,
        consoleCall("POST", "/tables", "admin", { name: "p.d.two", schema: alpha, strategy: "nope" }).status,
        consoleCall("POST", "/tables", "admin", { name: "p.d.three", schema: "none_such" }).status,
        consoleCall("POST", "/tables", "admin", { name: "p.d.four", schema: alpha, strategy: "full", n: 4 }).status,
        consoleCall("POST", "/rls", "admin", {
            table_name: "p.d.beta",
            column_name: "unit_id",
            unit_type: "unit",
        }).status,
        consoleCall("DELETE", `/schemas?name=eq.${alpha}`, "admin").status,
    ];

    /** @type {Array<[string, number]>} */
    const expectedStatuses = [
        ["a name with a capital letter and a dash is a 400", 400],
        ["a schema that exists already is a 409", 409],
        ["a table with an unknown strategy is a 400", 400],
        ["a table of a schema that doesn't exist is a 409", 409],
        ["a full table with a partition count is a 400", 400],
        ["a rule for a schema without a claim is a 400", 400],
        ["a schema that a table references can't be deleted: a 409", 409],
    ];

    expectedStatuses.forEach(([description, status], index) => expect(description, statuses[index] === status));

    expectEach(
        ["rbac", "console", "data_proxy", "dbos", "public", "pg_custom"],
        (name) => `a source schema can't take the name ${name} of a schema that the system owns`,
        (name) => consoleCall("POST", "/schemas", "admin", { name }).status === 400,
    );

    expect(
        "a run for an unknown schema is refused",
        consoleCall("POST", "/rpc/start_sync", "admin", { schemas: ["console_bad"] }).status === 400,
    );

    expect(
        "a run for a table of another schema is refused",
        consoleCall("POST", "/rpc/start_sync", "admin_other", { tables: ["p.d.beta"] }).status === 403,
    );

    expect(
        "a table that doesn't exist gets the same refusal as a table outside the token",
        consoleCall("GET", `/rpc/table_files?schema_name=${SCHEMA}&table_name=none_such`, "admin").status === 403,
    );

    const errors = rowsOf(consoleCall("GET", "/errors?select=recorded_at&limit=50", "admin"));

    expect(
        "the errors come newest first",
        errors.every(
            (error, index) => index === 0 || String(errors[index - 1].recorded_at) >= String(error.recorded_at),
        ),
    );

    const full = {
        name: "p.d.full",
        strategy: "full",
        cache_ttl: 60,
        rls: [
            { column: "unit_id", unit_type: "unit" },
            { column: "region_id", unit_type: "region" },
        ],
    };

    const partitioned = {
        name: "p.d.part",
        strategy: "partitioned",
        n: 4,
        fallback: true,
        ducklake: { sort: ["created"], partitioning: [{ column: "created", transform: "day" }] },
    };

    const importName = "console_import";

    const entry = /** @param {unknown[]} tables */ (tables) => ({
        source: { type: "bigquery" },
        claim: "preferred_username",
        tables,
    });

    const load = /** @param {Record<string, unknown>} schemas */ (schemas) =>
        consoleCall("POST", "/rpc/import_config", "admin", { document: { schemas } }).status;

    expect("an admin imports a document", load({ [importName]: entry([full, partitioned]) }) === 204);

    expect(
        "the endpoint returns the document that was imported",
        documentBecomes(importName, entry([full, partitioned])),
    );

    const replaced = { ...full, rls: [{ column: "other", unit_type: "unit" }] };
    expect("a second import is accepted", load({ [importName]: entry([replaced]) }) === 204);

    expect(
        "importing again replaces the rules of a table and keeps the other tables",
        documentBecomes(importName, entry([replaced, partitioned])),
    );

    expect(
        "a bad document is a 400",
        load({
            console_bad: { source: { type: "bigquery" }, tables: [{ name: "p.d.bad", strategy: "full", n: 4 }] },
        }) === 400,
    );

    expect("a bad document changes nothing", documentOf("console_bad") === undefined);
    ["p.d.full", "p.d.part", "p.d.beta"].forEach((name) => consoleCall("DELETE", `/tables?name=eq.${name}`, "admin"));

    expect(
        "deleting a table deletes its rules",
        waitUntil(10, () => consoleRows("/rls?table_name=like.p.d.*", "admin") === 1),
    );

    consoleCall("DELETE", "/tables?name=eq.p.d.one", "admin");
    [alpha, beta, importName].forEach((name) => consoleCall("DELETE", `/schemas?name=eq.${name}`, "admin"));

    expect(
        "the admin removes the schemas that the checks added",
        waitUntil(
            10,
            () => [alpha, beta, importName].every((name) => documentOf(name) === undefined),
            "the schemas to leave sync.json",
        ),
    );

    expect(
        "the admin removes the rules that the checks added",
        waitUntil(10, () => consoleRows("/rls?table_name=like.p.d.*", "admin") === 0, "the rules to go"),
    );
}

/** @type {Step[]} */
export const steps = [
    { scenario: "console", name: "console", stage: 260, run: () => console() },
    { scenario: "console", name: "surface", stage: 270, run: () => surface() },
    { scenario: "console", name: "behaviour", stage: 310, run: () => behaviour() },
];
