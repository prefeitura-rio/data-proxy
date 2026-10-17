/** @import { Kubernetes } from 'k6/x/kubernetes' */
import { consoleCall, postgrestCall } from "./console.js";
import { cronJobPodSpec, rcloneStore, runCommandJob, sqlEquals } from "./kubernetes.js";

const PROVISION_CRONJOB = "data-proxy-provision";

/**
 * Reports whether a command succeeds in a Job. The Job helpers throw when a command fails.
 * @param {() => void} run
 * @returns {boolean}
 */
function succeeds(run) {
    try {
        run();

        return true;
    } catch {
        return false;
    }
}

/**
 * Removes a schema, one table and one policy subject from the console. Provision then drops everything else.
 * @param {string} schema
 * @param {string} table
 * @param {string} subject
 */
export function removeSchemaFromConsole(schema, table, subject) {
    postgrestCall(schema, "DELETE", `/access_policy?subject=eq.${subject}`, "admin");
    consoleCall("DELETE", `/tables?name=eq.${table}`, "admin");
    consoleCall("DELETE", `/schemas?name=eq.${schema}`, "admin");
}

/**
 * The schema is gone from the application database.
 * @param {Kubernetes} k8s
 * @param {string} schema
 * @returns {boolean}
 */
export function appSchemaGone(k8s, schema) {
    return succeeds(() =>
        runCommandJob(k8s, "check-app-schema-gone", sqlEquals(namespaceCount(schema), "0", '"$PG_DATABASE_URL"')),
    );
}

/**
 * The metadata schema is gone from the catalog database.
 * @param {Kubernetes} k8s
 * @param {string} schema
 * @returns {boolean}
 */
export function catalogSchemaGone(k8s, schema) {
    return succeeds(() =>
        runCommandJob(
            k8s,
            "check-catalog-schema-gone",
            sqlEquals(namespaceCount(schema), "0", '"$DUCKLAKE_CATALOG_URL"'),
        ),
    );
}

/**
 * No data file is left under the prefix of the schema. The provision pod holds the object store settings.
 * @param {Kubernetes} k8s
 * @param {string} schema
 * @returns {boolean}
 */
export function filesGone(k8s, schema) {
    const podSpec = cronJobPodSpec(k8s, PROVISION_CRONJOB);
    const scheme = '"$([ "$S3_USE_SSL" = true ] && echo https || echo http)://$S3_ENDPOINT"';
    const prefix = `store:$S3_BUCKET/$DUCKLAKE_CATALOG_PATH/${schema}`;

    return succeeds(() =>
        runCommandJob(
            k8s,
            "check-files-gone",
            `${rcloneStore(scheme)}; test "$(rclone lsf "${prefix}" --recursive 2>/dev/null | wc -l)" = 0`,
            { podSpec, image: podSpec.containers[0].image },
        ),
    );
}

/**
 * @param {string} schema
 * @returns {string}
 */
const namespaceCount = (schema) => `SELECT count(*) FROM pg_namespace WHERE nspname = '${schema}'`;
