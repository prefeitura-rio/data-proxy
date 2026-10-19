/** @import { Kubernetes } from 'k6/x/kubernetes' */
import { sleep } from "k6";
import {
    clearProxyCache,
    clientToken,
    directPostgrest,
    FULL_TABLE,
    MIN_INSTANCES,
    MULTI_RLS_TABLE,
    PARTITIONED_TABLE,
    podsForComponent,
    proxyGet,
    readyInstances,
    rowsOf,
    runCommandJob,
    scenario,
    SCHEMA,
    SNAPSHOT_TABLE,
    snapshotHeader,
    snapshotValue,
    sourceHeader,
    TABLES,
    userToken,
} from "../lib/index.js";

const { expect } = scenario("ducklake");

/**
 * Verifies every table is served by the sources its configuration allows.
 * @param {string} token
 */
export function publication(token) {
    /** @type {Record<string, string>} */
    const expected = {
        [FULL_TABLE]: "ducklake",
        [SNAPSHOT_TABLE]: "ducklake",
        [MULTI_RLS_TABLE]: "ducklake",
        [PARTITIONED_TABLE]: "ducklake+bigquery",
    };

    TABLES.forEach((table) => {
        const response = proxyGet(`/${table}?limit=1`, token);
        expect(`${table} answers 200 after DuckLake publication`, response.status === 200);

        expect(
            `${table} is served by ${expected[table]} after DuckLake publication`,
            sourceHeader(response) === expected[table],
        );

        expect(`${table} reports the DuckLake snapshot it read`, /^[0-9]+$/.test(snapshotHeader(response)));
    });
}

/**
 * Verifies the sync workers run and the schema has its metadata schema in the catalog database.
 * @param {Kubernetes} k8s
 */
export function catalogSync(k8s) {
    const syncPods = podsForComponent(k8s, "sync");
    expect("at least one sync pod is running", syncPods.length > 0);

    runCommandJob(
        k8s,
        "check-writer-catalog",
        `test "$(psql "$DUCKLAKE_CATALOG_URL" -tAc "SELECT count(*) FROM information_schema.schemata WHERE schema_name = '${SCHEMA}'")" = 1`,
    );
}

/**
 * Verifies every live PostgreSQL instance serves the published snapshot from its own catalog.
 * @param {Kubernetes} k8s
 * @param {string} token
 */
export function instanceCatalogs(k8s, token) {
    const snapshot = snapshotValue(token);
    const instances = readyInstances(k8s);
    expect("the cluster has a primary and a standby, and both are ready", instances.length >= MIN_INSTANCES);

    for (const instance of instances) {
        const host = instance.status?.podIP ?? "";
        const dsn = `$(echo "$PG_DATABASE_URL" | sed 's#@[^:/]*#@${host}#')`;

        runCommandJob(
            k8s,
            `instance-snapshot-${instance.metadata.name}`,
            `test "$(psql "${dsn}" -tAc ${JSON.stringify(`SELECT ${SCHEMA}.dl_latest_snapshot()`)})" = ${JSON.stringify(snapshot)}`,
        );
    }
}

/**
 * Verifies the change feed: only read_ducklake reads it, and it returns only the changes of the schemas and the units of the token.
 */
export function changeFeed() {
    const token = clientToken("admin");
    const otherToken = clientToken("admin_other");
    const latest = Number(snapshotValue(token));
    const changeTypes = ["insert", "delete", "update_preimage", "update_postimage"];

    const feeds = [
        {
            table: FULL_TABLE,
            columns: "unit_id",
            permitted: /** @param {Record<string, unknown>} row */ (row) => row.unit_id === "unit_1",
        },
        {
            table: MULTI_RLS_TABLE,
            columns: "region_id,group_id",
            permitted: /** @param {Record<string, unknown>} row */ (row) =>
                row.region_id === "region_1" || row.group_id === "group_1",
        },
    ];

    for (const feed of feeds) {
        const path = `/rpc/dl_changes_${feed.table}?select=change_snapshot_id,change_type,${feed.columns}&start_snapshot=0`;
        const all = proxyGet(path, token);
        const rows = rowsOf(all);
        const bounded = proxyGet(`${path}&end_snapshot=${latest}`, token);
        const refused = proxyGet(path, clientToken("api"));
        const other = proxyGet(path, otherToken);
        expect(`${feed.table} change feed answers 200`, all.status === 200);
        expect(`${feed.table} change feed returns rows`, rows.length > 0);
        expect(`${feed.table} change feed returns only permitted units`, rows.every(feed.permitted));

        expect(
            `${feed.table} change feed names a known change type`,
            rows.every((row) => changeTypes.includes(String(row.change_type))),
        );

        expect(
            `${feed.table} change feed stays within the published snapshots`,
            rows.every((row) => Number(row.change_snapshot_id) <= latest),
        );

        expect(`${feed.table} change feed with an end snapshot answers 200`, bounded.status === 200);

        expect(
            `${feed.table} change feed ends at the latest snapshot by default`,
            rowsOf(bounded).length === rows.length,
        );

        expect(`${feed.table} change feed is refused without read_ducklake`, refused.status === 403);
        expect(`${feed.table} change feed answers 200 to an admin of another schema`, other.status === 200);
        expect(`${feed.table} change feed is empty for an admin of another schema`, rowsOf(other).length === 0);
    }
}

/**
 * Verifies PostgREST serves data from the catalog of the instance that it reaches.
 * @param {string} token
 */
export function readerMount(token) {
    const response = directPostgrest(`/${FULL_TABLE}?limit=1`, token);
    expect("PostgREST answers 200 from the instance-local DuckLake catalog", response.status === 200);
    expect("PostgREST serves rows from the instance-local DuckLake catalog", rowsOf(response).length > 0);
}

/**
 * Verifies a table without fallbacks reports DuckLake as its only source.
 * @param {string} token
 * @param {Kubernetes} k8s
 */
export function source(token, k8s) {
    clearProxyCache(k8s);
    sleep(1);
    const response = proxyGet(`/${FULL_TABLE}?select=id&limit=1`, token);
    expect("a table without fallbacks answers 200", response.status === 200);
    expect("a table without fallbacks is served from DuckLake only", sourceHeader(response) === "ducklake");
}

/** @type {Step[]} */
export const steps = [
    { scenario: "ducklake", name: "publication", stage: 110, run: () => publication(userToken()) },
    { scenario: "ducklake", name: "changeFeed", stage: 120, run: () => changeFeed() },
    { scenario: "ducklake", name: "catalogSync", stage: 130, run: ({ k8s }) => catalogSync(k8s) },
    {
        scenario: "ducklake",
        name: "instanceCatalogs",
        stage: 140,
        run: ({ k8s }) => instanceCatalogs(k8s, userToken()),
    },
    { scenario: "ducklake", name: "readerMount", stage: 180, run: () => readerMount(userToken()) },
    { scenario: "ducklake", name: "source", stage: 190, run: ({ k8s }) => source(userToken(), k8s) },
];
