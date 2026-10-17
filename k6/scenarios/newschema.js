/** @import { Step } from '../lib/index.js' */
/** @import { Kubernetes } from 'k6/x/kubernetes' */
import {
    clientToken,
    consoleCall,
    consoleRows,
    documentBecomes,
    documentOf,
    appSchemaGone,
    catalogSchemaGone,
    filesGone,
    NEW_SCHEMA,
    NEW_SCHEMA_SOURCE,
    NEW_SCHEMA_UNIT_1_IDS,
    postgrestCall,
    proxyGet,
    removeSchemaFromConsole,
    rowsOf,
    runOf,
    scenario,
    sourceHeader,
    stable,
    startRun,
    waitForRun,
    waitUntil,
} from "../lib/index.js";

const { expect, requirePrecondition } = scenario("newschema");

/**
 * A schema edit queues a workflow, and the sync worker makes a Job of the provision template, so it takes seconds.
 */
const PROVISION_TIMEOUT_SECONDS = 90;
const RUN_TIMEOUT_SECONDS = 600;
const SERVE_TIMEOUT_SECONDS = 240;
const TABLE = NEW_SCHEMA_SOURCE.split(".").pop() ?? "";
const PROFILE = { "Accept-Profile": NEW_SCHEMA };
const SUBJECT = "test_admin";

/**
 * Verifies the path of a new schema from the console to the proxy: the console accepts the rows, `provision` creates
 * the schema, a run publishes the table, and the table is served to the subject that has a policy row, and to nobody else.
 * @param {Kubernetes} k8s
 */
export function newSchema(k8s) {
    removeSchemaFromConsole(NEW_SCHEMA, NEW_SCHEMA_SOURCE, SUBJECT);
    waitUntil(PROVISION_TIMEOUT_SECONDS, () => appSchemaGone(k8s, NEW_SCHEMA), "leftovers of an earlier run to go");

    try {
        const created = [
            consoleCall("POST", "/schemas", "admin", { name: NEW_SCHEMA, claim: "preferred_username" }),
            consoleCall("POST", "/tables", "admin", { name: NEW_SCHEMA_SOURCE, schema: NEW_SCHEMA, strategy: "full" }),
            consoleCall("POST", "/rls", "admin", {
                table_name: NEW_SCHEMA_SOURCE,
                column_name: "unit_id",
                unit_type: "unit",
            }),
        ];

        ["the schema", "the table", "the rule"].forEach((what, index) =>
            requirePrecondition(`the console accepts ${what} of the new schema`, created[index]?.status === 201, {}),
        );

        expect(
            "sync.json lists the new schema",
            documentBecomes(NEW_SCHEMA, {
                source: { type: "bigquery" },
                claim: "preferred_username",
                tables: [
                    { name: NEW_SCHEMA_SOURCE, strategy: "full", rls: [{ column: "unit_id", unit_type: "unit" }] },
                ],
            }),
        );

        expect(
            "provision creates the schema and its access policy",
            waitUntil(
                PROVISION_TIMEOUT_SECONDS,
                () => {
                    const health = rowsOf(consoleCall("GET", `/health?name=eq.${NEW_SCHEMA}`, "admin"))[0];

                    return health?.schema_exists === true && health.access_policy === true;
                },
                "provision to create the new schema",
            ),
        );

        const runId = startRun([NEW_SCHEMA]);
        requirePrecondition("an operator starts a run for the new schema", runId !== null, {});
        if (runId === null) return;
        expect("the run for the new schema ends with success", waitForRun(runId, RUN_TIMEOUT_SECONDS) === "SUCCESS");
        expect("the run publishes the table", Number(runOf(runId)?.task_count) > 0);

        expect(
            "the admin adds a policy row to the new schema",
            postgrestCall(NEW_SCHEMA, "POST", "/access_policy", "admin", {
                subject: SUBJECT,
                unit_type: "unit",
                unit_id: "unit_1",
            }).status === 201,
        );

        const path = `/${TABLE}?select=id,unit_id&limit=20`;
        let served = proxyGet(path, clientToken("admin"), PROFILE);

        expect(
            "the proxy serves the new table",
            waitUntil(
                SERVE_TIMEOUT_SECONDS,
                () => {
                    served = proxyGet(path, clientToken("admin"), PROFILE);

                    return served.status === 200 && rowsOf(served).length > 0;
                },
                "the proxy to serve the new table",
            ),
        );

        expect("the new table is read from DuckLake", sourceHeader(served).startsWith("ducklake"));

        expect(
            "the rows of the new table belong to the unit of the policy row",
            rowsOf(served).every((row) => row.unit_id === "unit_1"),
        );

        const other = proxyGet(path, clientToken("api"), PROFILE);

        const ids = rowsOf(served)
            .map((row) => Number(row.id))
            .sort((a, b) => a - b);

        expect("the served rows are the seeded rows of unit_1", stable(ids) === stable(NEW_SCHEMA_UNIT_1_IDS));

        const count = rowsOf(proxyGet(`/${TABLE}?select=count()`, clientToken("admin"), PROFILE));

        expect(
            "the count of the new table is the count of the seeded rows of unit_1",
            Number(count[0]?.count) === NEW_SCHEMA_UNIT_1_IDS.length,
        );

        expect("a token for another schema reads no row of the new table", rowsOf(other).length === 0);
        expect("the console still lists the new schema", consoleRows(`/schemas?name=eq.${NEW_SCHEMA}`, "admin") === 1);
    } finally {
        removeSchemaFromConsole(NEW_SCHEMA, NEW_SCHEMA_SOURCE, SUBJECT);

        expect(
            "the new schema is gone from sync.json",
            waitUntil(30, () => documentOf(NEW_SCHEMA) === undefined),
        );

        expect(
            "provision drops the schema of the database",
            waitUntil(PROVISION_TIMEOUT_SECONDS, () => appSchemaGone(k8s, NEW_SCHEMA), "provision to drop the schema"),
        );

        expect(
            "provision drops the catalog schema",
            waitUntil(
                PROVISION_TIMEOUT_SECONDS,
                () => catalogSchemaGone(k8s, NEW_SCHEMA),
                "provision to drop the catalog",
            ),
        );

        expect(
            "provision purges the data files",
            waitUntil(PROVISION_TIMEOUT_SECONDS, () => filesGone(k8s, NEW_SCHEMA), "provision to purge the files"),
        );
    }
}

/** @type {Step[]} */
export const steps = [{ scenario: "newschema", name: "newSchema", stage: 350, run: ({ k8s }) => newSchema(k8s) }];
