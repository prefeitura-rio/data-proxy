/** @import { Kubernetes } from 'k6/x/kubernetes' */
import {
    appSchemaGone,
    consoleCall,
    cronJobPodSpec,
    removeSchemaFromConsole,
    rowsOf,
    runCommandJob,
    scenario,
    waitUntil,
} from "../lib/index.js";

const { expect, requirePrecondition } = scenario("query_scope");

const SCHEMAS = ["e2e_scope_a", "e2e_scope_b"];
const PROVISION_TIMEOUT_SECONDS = 90;

/**
 * Runs SQL in the catalog database from the provision pod, which holds the owner login. It reports whether the output
 * has the text.
 * @param {Kubernetes} k8s
 * @param {string} label
 * @param {string} statement
 * @param {string} expected
 * @returns {boolean}
 */
function catalogSays(k8s, label, statement, expected) {
    const podSpec = cronJobPodSpec(k8s, "data-proxy-provision");

    try {
        runCommandJob(
            k8s,
            label,
            `psql "$DUCKLAKE_ADMIN_URL" -v ON_ERROR_STOP=0 -c ${JSON.stringify(statement)} 2>&1 | grep -q ${JSON.stringify(expected)}`,
            { podSpec, image: podSpec.containers[0].image },
        );

        return true;
    } catch {
        return false;
    }
}

/**
 * Verifies the second layer of the query tab: the role of one schema reads the metadata of that schema and is refused
 * on the metadata of another. The signed links of the third layer have their own tests in the console.
 * @param {Kubernetes} k8s
 */
export function queryScope(k8s) {
    const [a, b] = SCHEMAS;

    try {
        for (const name of SCHEMAS) {
            requirePrecondition(
                `the console accepts the schema ${name}`,
                consoleCall("POST", "/schemas", "admin", { name, claim: "preferred_username" }).status === 201,
                {},
            );
        }

        for (const name of SCHEMAS) {
            expect(
                `provision creates the schema ${name}`,
                waitUntil(
                    PROVISION_TIMEOUT_SECONDS,
                    () => rowsOf(consoleCall("GET", `/health?name=eq.${name}`, "admin"))[0]?.access_policy === true,
                    "provision to create the schema",
                ),
            );
        }

        const prepare = SCHEMAS.map((name) => `CREATE TABLE IF NOT EXISTS ${name}.probe (path text)`).join("; ");
        expect("the metadata schemas exist in the catalog", catalogSays(k8s, "scope-prepare", `${prepare}; SELECT 'prepared'`, "prepared"));

        expect(
            "the signer is a member of the role of each schema",
            catalogSays(
                k8s,
                "scope-members",
                `SELECT 'members ' || bool_and(pg_has_role('console_signer', 'schema_' || s, 'member')) FROM unnest(ARRAY['${a}', '${b}']) AS s`,
                "members true",
            ),
        );

        expect(
            "the role of a schema reads the metadata of that schema",
            catalogSays(k8s, "scope-own", `SET ROLE schema_${a}; SELECT 'read ' || count(*) FROM ${a}.probe`, "read 0"),
        );

        expect(
            "the role of a schema is refused on the metadata of another schema",
            catalogSays(
                k8s,
                "scope-other",
                `SET ROLE schema_${a}; SELECT count(*) FROM ${b}.probe`,
                `permission denied for schema ${b}`,
            ),
        );

        expect(
            "the role of a schema can't write its own metadata",
            catalogSays(
                k8s,
                "scope-write",
                `SET ROLE schema_${a}; INSERT INTO ${a}.probe VALUES ('x')`,
                `permission denied for table probe`,
            ),
        );

        expect(
            "the role of a schema is not a member of PUBLIC rights on another schema",
            catalogSays(
                k8s,
                "scope-usage",
                `SELECT 'usage ' || has_schema_privilege('schema_${a}', '${b}', 'USAGE')`,
                "usage false",
            ),
        );
    } finally {
        for (const name of SCHEMAS) {
            removeSchemaFromConsole(name, "none.none.none", "none");
        }

        for (const name of SCHEMAS) {
            waitUntil(PROVISION_TIMEOUT_SECONDS, () => appSchemaGone(k8s, name), "provision to drop the schema");
        }

        expect(
            "provision drops the role of a removed schema",
            catalogSays(k8s, "scope-dropped", `SELECT 'roles ' || count(*) FROM pg_roles WHERE rolname LIKE 'schema\\_e2e\\_scope\\_%'`, "roles 0"),
        );
    }
}

/** @type {Step[]} */
export const steps = [{ scenario: "query_scope", name: "queryScope", stage: 365, run: ({ k8s }) => queryScope(k8s) }];
