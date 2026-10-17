/** @import { Step } from '../lib/index.js' */
/** @import { Kubernetes } from 'k6/x/kubernetes' */
import {
    checkSql,
    cronJobPodSpec,
    MAINTENANCE_CRONJOB,
    rcloneStore,
    runCommandJob,
    runCronJob,
    runSqlJob,
    SCHEMA,
    waitForJob,
} from "../lib/index.js";

const BACKUP_CRONJOB = __ENV.BACKUP_CRONJOB || "data-proxy-backup";
const STALE_VIEW = `${SCHEMA}.e2e_stale_view`;

/**
 * Verifies the deployed backup CronJob uploads its dumps and prunes the access log.
 * @param {Kubernetes} k8s
 */
export function backup(k8s) {
    const podSpec = cronJobPodSpec(k8s, BACKUP_CRONJOB);
    const image = podSpec.containers[0].image;
    const base = `base="store:$S3_BUCKET/$BACKUP_PREFIX/${SCHEMA}"`;

    runSqlJob(
        k8s,
        "create-stale-log",
        `INSERT INTO ${SCHEMA}.access_log (subject, unit_type, unit_id, action, changed_at) VALUES ('e2e_stale', 'unit', 'unit_9', 'delete', now() - interval '365 days')`,
    );

    runCommandJob(k8s, "reset-backup-folder", `${rcloneStore()}; ${base}; rclone purge "$base" || true`, {
        podSpec,
        image,
    });

    waitForJob(k8s, runCronJob(k8s, BACKUP_CRONJOB));

    runCommandJob(
        k8s,
        "check-backup-objects",
        `${rcloneStore()}; ${base}; latest="$(rclone lsf "$base" --dirs-only | sort | tail -n 1)"; test "$(rclone lsf "$base/$latest" | sort | tr '\\n' ' ')" = "access_log.dump access_policy.dump "`,
        { podSpec, image },
    );

    checkSql(k8s, "check-pruned-log", `SELECT count(*) FROM ${SCHEMA}.access_log WHERE subject = 'e2e_stale'`, "0");
}

const RESTORE_SCHEMA = "restore_check";

/**
 * The shell that restores one dump of the latest backup into a scratch schema, with the table of the source as its shape.
 * @param {string} table
 * @returns {string}
 */
const RESTORE_DUMPS = (table) =>
    `pg_restore --data-only -f - /tmp/restore/${table}.dump | sed 's/^COPY ${SCHEMA}\\./COPY ${RESTORE_SCHEMA}./' | psql "$PG_DATABASE_URL" -v ON_ERROR_STOP=1 -q`;

/**
 * The text of a query result in the source and in the scratch schema, so that a Job can compare them.
 * @param {string} schema
 * @returns {string}
 */
const ROWS_DIGEST = (schema) =>
    `psql "$PG_DATABASE_URL" -tAc "SELECT count(*) || ':' || coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '') FROM ${schema}.access_policy t"`;

/**
 * Verifies that the latest backup restores: the dumps go into a scratch schema, the policy rows equal the live rows,
 * and the log rows that the backup pruned from the live table are back in the restored copy.
 * @param {Kubernetes} k8s
 */
export function restore(k8s) {
    const podSpec = cronJobPodSpec(k8s, BACKUP_CRONJOB);
    const image = podSpec.containers[0].image;
    const base = `base="store:$S3_BUCKET/$BACKUP_PREFIX/${SCHEMA}"`;
    const drop = `DROP SCHEMA IF EXISTS ${RESTORE_SCHEMA} CASCADE`;

    try {
        runSqlJob(
            k8s,
            "prepare-restore",
            `${drop}; CREATE SCHEMA ${RESTORE_SCHEMA}; CREATE TABLE ${RESTORE_SCHEMA}.access_policy (LIKE ${SCHEMA}.access_policy INCLUDING DEFAULTS); CREATE TABLE ${RESTORE_SCHEMA}.access_log (LIKE ${SCHEMA}.access_log INCLUDING DEFAULTS)`,
        );

        runCommandJob(
            k8s,
            "restore-latest-backup",
            [
                "set -e",
                rcloneStore(),
                base,
                'latest="$(rclone lsf "$base" --dirs-only | sort | tail -n 1)"',
                'mkdir -p /tmp/restore && rclone copy "$base/$latest" /tmp/restore',
                RESTORE_DUMPS("access_policy"),
                RESTORE_DUMPS("access_log"),
            ].join("\n"),
            { podSpec, image },
        );

        runCommandJob(
            k8s,
            "check-restored-policy",
            `test "$(${ROWS_DIGEST(SCHEMA)})" = "$(${ROWS_DIGEST(RESTORE_SCHEMA)})"`,
        );

        checkSql(
            k8s,
            "check-restored-log",
            `SELECT count(*) FROM ${RESTORE_SCHEMA}.access_log WHERE subject = 'e2e_stale'`,
            "1",
        );
    } finally {
        runSqlJob(k8s, "drop-restore-schema", drop);
    }
}

/**
 * Verifies the deployed maintenance CronJob drops a view that left the sync config.
 * @param {Kubernetes} k8s
 */
export function maintenance(k8s) {
    runSqlJob(k8s, "create-stale-view", `CREATE OR REPLACE VIEW ${STALE_VIEW} AS SELECT 1 AS id`);
    waitForJob(k8s, runCronJob(k8s, MAINTENANCE_CRONJOB));

    runCommandJob(
        k8s,
        "check-stale-view",
        `test "$(psql "$PG_DATABASE_URL" -tAc "SELECT to_regclass('${STALE_VIEW}') IS NULL")" = t`,
    );
}

/** @type {Step[]} */
export const steps = [
    { scenario: "jobs", name: "restore", stage: 175, run: ({ k8s }) => restore(k8s) },
    { scenario: "jobs", name: "maintenance", stage: 160, run: ({ k8s }) => maintenance(k8s) },
    { scenario: "jobs", name: "backup", stage: 170, run: ({ k8s }) => backup(k8s) },
];
