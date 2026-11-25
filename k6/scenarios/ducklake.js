/** @import { Response as K6Response } from 'k6/http' */
/** @import { Kubernetes } from 'k6/x/kubernetes' */
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
    readRows,
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
function catalogSync(k8s) {
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
function instanceCatalogs(k8s, token) {
    const snapshot = snapshotValue(token);
    const instances = readyInstances(k8s);
    expect("the cluster has a primary and a standby, and both are ready", instances.length >= MIN_INSTANCES);

    for (const instance of instances) {
        const host = instance.status?.podIP ?? "";
        const dsn = `$(echo "$PG_DATABASE_URL" | sed 's#@[^:/]*#@${host}#')`;

        runCommandJob(
            k8s,
            `instance-snapshot-${instance.metadata.name}`,
            `test "$(psql "${dsn}" -tAc ${JSON.stringify(`SELECT ${SCHEMA}.snapshot()`)})" = ${JSON.stringify(snapshot)}`,
        );
    }
}

/**
 * Verifies the change feed: only read_ducklake reads it, and it returns only the changes of the units of the token.
 */
function changeFeed() {
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
        // The row columns are in the object `data`, so each one is read by a JSON path with its own name.
        const columns = feed.columns
            .split(",")
            .map((name) => `${name}:data->>${name}`)
            .join(",");
        const path = `/rpc/changes?table=${feed.table}&select=change_snapshot_id,change_type,${columns}&start_snapshot=0`;
        const all = proxyGet(path, token);
        const rows = readRows(all);
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
            readRows(bounded).length === rows.length,
        );

        expect(`${feed.table} change feed is refused without read_ducklake`, refused.status === 403);
        expect(`${feed.table} change feed is refused to a console client without read_ducklake`, other.status === 403);
    }
}

/**
 * Reads the first object of a JSON answer that is a list, or null.
 * @param {K6Response} response
 * @returns {Record<string, unknown> | null}
 */
function firstRow(response) {
    const rows = readRows(response);

    return rows.length > 0 ? rows[0] : null;
}

/**
 * Verifies the generic functions of the schema through the proxy: `agg`, `hll` and the refusal of a table that the
 * token cannot use. One function of the schema serves every table, and the table is in the query string.
 */
function genericFunctions() {
    const token = clientToken("admin");
    const base = `/rpc/hll?table=${FULL_TABLE}&column=unit_id`;

    // hll: an estimate within 5 percent of the exact number of distinct units.
    const exactRows = readRows(proxyGet(`/${FULL_TABLE}?select=unit_id&limit=100000`, token));
    const exact = new Set(exactRows.map((row) => String(row.unit_id))).size;
    const estimated = proxyGet(base, token);
    const estimate = Number(firstRow(estimated)?.estimate);

    expect("hll answers 200", estimated.status === 200);
    expect("hll answers one object with an estimate", Number.isFinite(estimate));
    expect(
        "hll estimate is within 5% of the exact count",
        exact > 0 && Math.abs(estimate - exact) / exact < 0.05,
    );

    const grouped = proxyGet(`${base}&group_by=unit_id`, token);
    expect("hll with group_by answers 200", grouped.status === 200);
    expect(
        "hll with group_by answers one estimate for each unit",
        readRows(grouped).length === exact && readRows(grouped).every((row) => Number(row.estimate) >= 1),
    );

    // hll refuses what it cannot count.
    expect(
        "hll refuses an unknown table",
        [400, 403].includes(proxyGet(`/rpc/hll?table=nothing_here&column=unit_id`, token).status),
    );
    expect(
        "hll refuses an unknown column",
        [400, 403].includes(proxyGet(`/rpc/hll?table=${FULL_TABLE}&column=nothing_here`, token).status),
    );
    expect(
        "hll refuses a precision out of range",
        [400, 403].includes(proxyGet(`${base}&lg_k=99`, token).status),
    );
    expect(
        "hll refuses a hostile table name",
        [400, 403].includes(proxyGet(`/rpc/hll?table=${encodeURIComponent('x"; DROP TABLE y;--')}&column=unit_id`, token).status),
    );
    expect(
        "hll is refused without read_api",
        proxyGet(base, clientToken("policy")).status === 403,
    );
    expect(
        "hll does not read a table of another schema",
        [400, 403].includes(proxyGet(base, clientToken("admin_other")).status),
    );

    // agg: the function answers null without an aggregate in the filter, and never fails.
    const bare = directPostgrest(`/rpc/agg?table=${FULL_TABLE}`, token);
    expect("agg answers null or 200 for a request without an aggregate", bare.status === 200 && String(bare.body).trim() === "null");
    expect(
        "agg refuses an unknown table",
        [400, 403].includes(directPostgrest(`/rpc/agg?table=nothing_here`, token).status),
    );
    expect(
        "agg is refused without read_api",
        directPostgrest(`/rpc/agg?table=${FULL_TABLE}`, clientToken("policy")).status === 403,
    );

    // An aggregate through the proxy runs in DuckDB and gives the counts of the table.
    const counted = proxyGet(`/${FULL_TABLE}?select=unit_id,n:count()`, token);
    const perUnit = new Map();
    exactRows.forEach((row) => perUnit.set(String(row.unit_id), (perUnit.get(String(row.unit_id)) ?? 0) + 1));

    expect("an aggregate through the proxy answers 200", counted.status === 200);
    expect("an aggregate through the proxy is answered by DuckDB", sourceHeader(counted) === "ducklake");
    expect(
        "an aggregate through the proxy equals the count of each unit",
        readRows(counted).length === perUnit.size &&
            readRows(counted).every((row) => perUnit.get(String(row.unit_id)) === Number(row.n)),
    );
}

/**
 * Verifies that the change feed refuses a table that is not served, and the old function of a table is gone.
 */
function changeFeedRefusals() {
    const token = clientToken("admin");

    expect(
        "the change feed refuses a table that is not served",
        proxyGet(`/rpc/changes?table=nothing_here&start_snapshot=0`, token).status === 403,
    );
    expect(
        "the change feed of a table name that holds SQL is refused",
        proxyGet(`/rpc/changes?table=${encodeURIComponent("x'; DROP TABLE y;--")}&start_snapshot=0`, token).status === 403,
    );
    expect(
        "the functions with the old dl_ names are gone",
        ["dl_chg?table=nothing_here&start_snapshot=0", "dl_table_files?table=nothing_here", "dl_latest_snapshot"].every(
            (path) => proxyGet(`/rpc/${path}`, token).status === 404,
        ),
    );
    expect(
        "the function of one table, which the generic function replaced, is gone",
        proxyGet(`/rpc/${FULL_TABLE}_dl_chg?start_snapshot=0`, token).status === 404,
    );
    expect(
        "the aggregate function of one table, which the generic function replaced, is gone",
        proxyGet(`/rpc/${FULL_TABLE}_agg`, token).status === 404,
    );
}

/**
 * Verifies PostgREST serves data from the catalog of the instance that it reaches.
 * @param {string} token
 */
function readerMount(token) {
    const response = directPostgrest(`/${FULL_TABLE}?limit=1`, token);
    expect("PostgREST answers 200 from the instance-local DuckLake catalog", response.status === 200);
    expect("PostgREST serves rows from the instance-local DuckLake catalog", readRows(response).length > 0);
}

/**
 * Verifies a table without fallbacks reports DuckLake as its only source.
 * @param {string} token
 * @param {Kubernetes} k8s
 */
export function source(token, k8s) {
    clearProxyCache(k8s);
    const response = proxyGet(`/${FULL_TABLE}?select=id&limit=1`, token);
    expect("a table without fallbacks answers 200", response.status === 200);
    expect("a table without fallbacks is served from DuckLake only", sourceHeader(response) === "ducklake");
}

/** @type {Step[]} */
export const steps = [
    { scenario: "ducklake", name: "publication", stage: 110, run: () => publication(userToken()) },
    { scenario: "ducklake", name: "changeFeed", stage: 120, run: () => changeFeed() },
    { scenario: "ducklake", name: "genericFunctions", stage: 125, run: () => genericFunctions() },
    { scenario: "ducklake", name: "changeFeedRefusals", stage: 126, run: () => changeFeedRefusals() },
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
