/** @import { Kubernetes } from 'k6/x/kubernetes' */
import {
    catalogTableGone,
    identityExists,
    identityGone,
    identityMade,
    consoleCall,
    appSchemaGone,
    postgrestCall,
    removeSchemaFromConsole,
    rowsOf,
    runOf,
    scenario,
    stateGone,
    startRun,
    tableFilesGone,
    waitForRun,
    waitUntil,
    NEW_SCHEMA_SOURCE,
} from "../lib/index.js";

const { expect, requirePrecondition } = scenario("cleanup");

const SCHEMA = "e2e_cleanup";
const PROVISION_TIMEOUT_SECONDS = 90;
const RUN_TIMEOUT_SECONDS = 600;
const CLEANUP_TIMEOUT_SECONDS = 120;
const TABLE = NEW_SCHEMA_SOURCE.split(".").pop() ?? "";
const SUBJECT = "test_admin";
const STALE_IDENTITY = "sig_e2e_stale";

/**
 * Verifies that deleting a table through PostgREST removes its DuckLake table, its data files and its state:
 * a trigger queues the cleanup workflow, and the sync worker runs it.
 * @param {Kubernetes} k8s
 */
export function cleanup(k8s) {
    removeSchemaFromConsole(SCHEMA, NEW_SCHEMA_SOURCE, SUBJECT);
    waitUntil(PROVISION_TIMEOUT_SECONDS, () => appSchemaGone(k8s, SCHEMA), "leftovers of an earlier run to go");

    requirePrecondition("an identity of a schema that does not exist is made", identityMade(k8s, STALE_IDENTITY), {});
    requirePrecondition("the identity is in the object store", identityExists(k8s, STALE_IDENTITY), {});

    try {
        const created = [
            consoleCall("POST", "/schemas", "admin", { name: SCHEMA, claim: "preferred_username" }),
            consoleCall("POST", "/tables", "admin", { name: NEW_SCHEMA_SOURCE, schema: SCHEMA, strategy: "full" }),
        ];

        ["the schema", "the table"].forEach((what, index) =>
            requirePrecondition(`the console accepts ${what}`, created[index]?.status === 201, {}),
        );

        expect(
            "provision creates the schema",
            waitUntil(
                PROVISION_TIMEOUT_SECONDS,
                () => rowsOf(consoleCall("GET", `/health?name=eq.${SCHEMA}`, "admin"))[0]?.access_policy === true,
                "provision to create the schema",
            ),
        );

        const runId = startRun([SCHEMA]);
        requirePrecondition("an operator starts a run", runId !== null, {});
        if (runId === null) return;
        expect("the run ends with success", waitForRun(runId, RUN_TIMEOUT_SECONDS) === "SUCCESS");
        expect("the run publishes the table", Number(runOf(runId)?.task_count) > 0);

        expect(
            "the table has state before it is deleted",
            !stateGone(k8s, NEW_SCHEMA_SOURCE),
        );

        expect(
            "an admin deletes the table",
            consoleCall("DELETE", `/tables?name=eq.${NEW_SCHEMA_SOURCE}`, "admin").status === 200,
        );

        expect(
            "the cleanup drops the DuckLake table",
            waitUntil(CLEANUP_TIMEOUT_SECONDS, () => catalogTableGone(k8s, SCHEMA, TABLE), "the cleanup to drop the table"),
        );

        expect(
            "the cleanup deletes the data files of the table",
            waitUntil(CLEANUP_TIMEOUT_SECONDS, () => tableFilesGone(k8s, SCHEMA, TABLE), "the cleanup to delete the files"),
        );

        expect(
            "the cleanup removes the state of the table",
            waitUntil(CLEANUP_TIMEOUT_SECONDS, () => stateGone(k8s, NEW_SCHEMA_SOURCE), "the cleanup to remove the state"),
        );

        expect(
            "the cleanup removes the S3 identity of a schema that is not in the console",
            waitUntil(CLEANUP_TIMEOUT_SECONDS, () => identityGone(k8s, STALE_IDENTITY), "the cleanup to remove the identity"),
        );

        expect(
            "the schema itself stays",
            consoleCall("GET", `/schemas?name=eq.${SCHEMA}`, "admin").status === 200 &&
                postgrestCall(SCHEMA, "GET", "/access_policy", "admin").status === 200,
        );
    } finally {
        removeSchemaFromConsole(SCHEMA, NEW_SCHEMA_SOURCE, SUBJECT);
        waitUntil(PROVISION_TIMEOUT_SECONDS, () => appSchemaGone(k8s, SCHEMA), "provision to drop the schema");
    }
}

/** @type {Step[]} */
export const steps = [{ scenario: "cleanup", name: "cleanup", stage: 360, run: ({ k8s }) => cleanup(k8s) }];
