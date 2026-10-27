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
 * The catalog has no live table of that name in the metadata schema.
 * @param {Kubernetes} k8s
 * @param {string} schema
 * @param {string} table
 * @returns {boolean}
 */
export function catalogTableGone(k8s, schema, table) {
    const query = `SELECT count(*) FROM "${schema}".ducklake_table WHERE table_name = '${table}' AND end_snapshot IS NULL`;

    return succeeds(() =>
        runCommandJob(k8s, "check-catalog-table-gone", sqlEquals(query, "0", '"$DUCKLAKE_CATALOG_URL"')),
    );
}

/**
 * No data file is left under the folder of one table.
 * @param {Kubernetes} k8s
 * @param {string} schema
 * @param {string} table
 * @returns {boolean}
 */
export function tableFilesGone(k8s, schema, table) {
    const podSpec = cronJobPodSpec(k8s, PROVISION_CRONJOB);
    const scheme = '"$([ "$S3_USE_SSL" = true ] && echo https || echo http)://$S3_ENDPOINT"';
    const prefix = `store:$S3_BUCKET/$DUCKLAKE_CATALOG_PATH/${schema}/main/${table}`;

    return succeeds(() =>
        runCommandJob(
            k8s,
            "check-table-files-gone",
            `${rcloneStore(scheme)}; test "$(rclone lsf "${prefix}" --recursive 2>/dev/null | wc -l)" = 0`,
            { podSpec, image: podSpec.containers[0].image },
        ),
    );
}

/**
 * The state and the errors of the table are gone from the application database.
 * @param {Kubernetes} k8s
 * @param {string} source
 * @returns {boolean}
 */
export function stateGone(k8s, source) {
    const query = `SELECT (SELECT count(*) FROM data_proxy.state WHERE table_name = '${source}') + (SELECT count(*) FROM data_proxy.errors WHERE fields ->> 'table' = '${source}')`;

    return succeeds(() => runCommandJob(k8s, "check-state-gone", sqlEquals(query, "0", '"$PG_DATABASE_URL"')));
}

const IAM = '--aws-sigv4 "aws:amz:us-east-1:iam" --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY"';
const IAM_URL = '"$([ "$S3_USE_SSL" = true ] && echo https || echo http)://$S3_ENDPOINT/"';

/**
 * Runs one IAM call of the object store from the provision pod, which holds the admin credential and has curl.
 * @param {Kubernetes} k8s
 * @param {string} label
 * @param {string} action the form of the call, such as `Action=ListUsers`
 * @param {string} [then="true"] a shell test on the answer, which is in the variable out
 * @returns {boolean}
 */
function iamCall(k8s, label, action, then = "true") {
    const podSpec = cronJobPodSpec(k8s, PROVISION_CRONJOB);
    const call = `out=$(curl -sf ${IAM} -X POST ${IAM_URL} -H "Content-Type: application/x-www-form-urlencoded" -d "${action}&Version=2010-05-08")`;

    return succeeds(() =>
        runCommandJob(k8s, label, `${call}; ${then}`, { podSpec, image: podSpec.containers[0].image }),
    );
}

/**
 * Makes an identity in the object store, as the console does for a schema.
 * @param {Kubernetes} k8s
 * @param {string} user
 * @returns {boolean}
 */
export function identityMade(k8s, user) {
    return iamCall(k8s, "make-identity", `Action=CreateUser&UserName=${user}`);
}

/**
 * The identity is gone from the object store.
 * @param {Kubernetes} k8s
 * @param {string} user
 * @returns {boolean}
 */
export function identityGone(k8s, user) {
    return iamCall(k8s, "check-identity-gone", "Action=ListUsers", `! echo "$out" | grep -q "<UserName>${user}</UserName>"`);
}

/**
 * The identity is in the object store.
 * @param {Kubernetes} k8s
 * @param {string} user
 * @returns {boolean}
 */
export function identityExists(k8s, user) {
    return iamCall(k8s, "check-identity-exists", "Action=ListUsers", `echo "$out" | grep -q "<UserName>${user}</UserName>"`);
}

/**
 * @param {string} schema
 * @returns {string}
 */
const namespaceCount = (schema) => `SELECT count(*) FROM pg_namespace WHERE nspname = '${schema}'`;
