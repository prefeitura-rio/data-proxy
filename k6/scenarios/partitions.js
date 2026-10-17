/** @import { Kubernetes } from 'k6/x/kubernetes' */
/** @import { Step } from '../lib/index.js' */
import { sleep } from "k6";
import {
    checkSql,
    clearProxyCache,
    directPostgrest,
    PARTITION_COLUMN,
    PARTITIONED_SOURCE,
    PARTITIONED_TABLE,
    proxyGet,
    rowsOf,
    rowsPerPartition,
    runSqlJob,
    scenario,
    snapshotValue,
    sourceHeader,
    STATE_DATABASE,
    SYNCED_PARTITIONS,
    triggerSync,
    userToken,
    waitForJob,
} from "../lib/index.js";

const { expect, requirePrecondition } = scenario("partitions");
const REMOVED_PARTITION = "19990101";

/**
 * Replays each kind of partition change by editing the stored manifest.
 *
 * The next sync must repair the manifest and leave the DuckLake rows unchanged,
 * because publication deletes the rows a changed partition replaces before it
 * inserts them again.
 * @param {Kubernetes} k8s
 */
export function changes(k8s) {
    const pinned = () => ({ "X-DuckLake-Snapshot": snapshotValue(userToken()) });
    const baseline = rowsPerPartition(userToken(), pinned()).counts;

    requirePrecondition("the partitioned table has a manifest to replay", baseline.size === SYNCED_PARTITIONS, {
        partitions: baseline.size,
    });

    const table = `table_name = '${PARTITIONED_SOURCE}'`;
    const oldest = "(SELECT min(key) FROM jsonb_each(state->'partitions'))";

    const cases = [
        {
            id: "added",
            name: "a partition missing from the manifest is added again",
            edit: `UPDATE data_proxy.state SET state = state #- ARRAY['partitions', ${oldest}] WHERE ${table}`,
            query: `SELECT count(*) FROM data_proxy.state, jsonb_object_keys(state->'partitions') WHERE ${table}`,
            broken: String(SYNCED_PARTITIONS - 1),
            healthy: String(SYNCED_PARTITIONS),
            publishes: true,
        },
        {
            id: "updated",
            name: "a partition with a stale signature is updated",
            edit: `UPDATE data_proxy.state SET state = jsonb_set(state, ARRAY['partitions', ${oldest}, 'signature'], to_jsonb('stale'::text)) WHERE ${table}`,
            query: `SELECT count(*) FROM data_proxy.state, jsonb_each(state->'partitions') WHERE ${table} AND value->>'signature' = 'stale'`,
            broken: "1",
            healthy: "0",
            publishes: true,
        },
        {
            id: "removed",
            name: "a partition that left the source is removed",
            edit: `UPDATE data_proxy.state SET state = jsonb_set(state, ARRAY['partitions', '${REMOVED_PARTITION}'], (SELECT value || jsonb_build_object('partition_id', '${REMOVED_PARTITION}', 'selection', (value->'selection') || jsonb_build_object('lower', '1999-01-01', 'upper', '1999-01-02')) FROM jsonb_each(state->'partitions') ORDER BY key LIMIT 1)) WHERE ${table}`,
            query: `SELECT count(*) FROM data_proxy.state WHERE ${table} AND (state->'partitions'->'${REMOVED_PARTITION}') IS NOT NULL`,
            broken: "1",
            healthy: "0",
            publishes: false,
        },
    ];

    for (const change of cases) {
        const before = Number(snapshotValue(userToken()));
        runSqlJob(k8s, `edit-${change.id}`, change.edit, STATE_DATABASE);
        checkSql(k8s, `check-${change.id}-edit`, change.query, change.broken, STATE_DATABASE);
        waitForJob(k8s, triggerSync(k8s));
        checkSql(k8s, `check-${change.id}-fix`, change.query, change.healthy, STATE_DATABASE);
        const after = rowsPerPartition(userToken(), pinned()).counts;
        expect(`${change.name}: the DuckLake partitions are the same`, after.size === baseline.size);

        expect(
            `${change.name}: the DuckLake rows are unchanged`,
            [...baseline].every(([date, rows]) => after.get(date) === rows),
        );

        if (change.publishes) {
            expect(`${change.name}: a new snapshot is published`, Number(snapshotValue(userToken())) > before);
        }
    }
}

/**
 * Verifies partitions outside DuckLake use the configured fallback.
 * @param {string} token
 * @param {Kubernetes} k8s
 */
export function fallbackRows(token, k8s) {
    clearProxyCache(k8s);
    sleep(1);
    const pinned = { "X-DuckLake-Snapshot": snapshotValue(token) };

    const localOldest = directPostgrest(
        `/${PARTITIONED_TABLE}?select=${PARTITION_COLUMN}&order=${PARTITION_COLUMN}.asc&limit=1`,
        token,
        pinned,
    );

    const localRows = rowsOf(localOldest);

    requirePrecondition("the local partitioned table answers", localOldest.status === 200, {
        status: localOldest.status,
    });

    requirePrecondition("the local partitioned table has an oldest partition", localRows.length > 0, {
        status: localOldest.status,
    });

    const oldest = String(localRows[0][PARTITION_COLUMN] || "");
    const filter = `${PARTITION_COLUMN}=lt.${oldest}&select=id,${PARTITION_COLUMN},unit_id&limit=20`;
    const local = directPostgrest(`/${PARTITIONED_TABLE}?${filter}`, token, pinned);

    requirePrecondition("the older partitions can be read from DuckLake", local.status === 200, {
        status: local.status,
        oldest,
    });

    requirePrecondition("an older partition is absent from DuckLake", rowsOf(local).length === 0, {
        status: local.status,
        oldest,
    });

    const response = proxyGet(`/${PARTITIONED_TABLE}?${filter}`, token);
    const rows = rowsOf(response);
    expect("an older partition answers 200", response.status === 200);
    expect("an older partition is served by the configured fallback", sourceHeader(response).startsWith("ducklake+"));
    expect("the fallback returns rows", rows.length > 0);

    expect(
        "fallback rows belong to the older partition",
        rows.every((row) => String(row[PARTITION_COLUMN]) < oldest),
    );

    expect(
        "fallback rows obey RLS",
        rows.every((row) => row.unit_id === "unit_1"),
    );
}

/**
 * Verifies a table split in half between DuckLake and its configured fallback.
 *
 * The seeded table has twice as many partitions as the sync keeps. DuckLake
 * holds the newest half and the fallback serves the older half.
 * @param {string} token
 * @param {Kubernetes} k8s
 */
export function splitSources(token, k8s) {
    clearProxyCache(k8s);
    sleep(1);
    const all = rowsPerPartition(token);

    const ducklake = rowsPerPartition(token, {
        "X-DuckLake-Snapshot": snapshotValue(token),
    });

    const allDates = [...all.counts.keys()].sort();
    const ducklakeDates = [...ducklake.counts.keys()].sort();
    const fallbackDates = allDates.filter((date) => !ducklake.counts.has(date));
    expect("the split table answers 200", all.response.status === 200);

    expect(
        "the split table is served by DuckLake and its configured fallback",
        sourceHeader(all.response).startsWith("ducklake+"),
    );

    expect("the pinned split table answers 200", ducklake.response.status === 200);
    expect("the pinned split table is served by DuckLake only", sourceHeader(ducklake.response) === "ducklake");

    requirePrecondition("the split table has partitions", allDates.length > 0, {
        all: allDates.length,
        ducklake: ducklakeDates.length,
    });

    requirePrecondition("DuckLake holds partitions of the split table", ducklakeDates.length > 0, {
        all: allDates.length,
        ducklake: ducklakeDates.length,
    });

    expect("DuckLake holds half of the partitions", ducklakeDates.length * 2 === allDates.length);
    expect("the fallback serves the other half of the partitions", fallbackDates.length === ducklakeDates.length);

    expect(
        "DuckLake holds the newest partitions",
        fallbackDates.every((date) => date < ducklakeDates[0]),
    );

    expect(
        "the combined answer keeps the DuckLake rows unchanged",
        ducklakeDates.every((date) => all.counts.get(date) === ducklake.counts.get(date)),
    );

    expect(
        "the fallback partitions have rows",
        fallbackDates.every((date) => (all.counts.get(date) || 0) > 0),
    );
}

/** @type {Step[]} */
export const steps = [
    { scenario: "partitions", name: "fallbackRows", stage: 200, run: ({ k8s }) => fallbackRows(userToken(), k8s) },
    { scenario: "partitions", name: "splitSources", stage: 210, run: ({ k8s }) => splitSources(userToken(), k8s) },
    { scenario: "partitions", name: "changes", stage: 230, run: ({ k8s }) => changes(k8s) },
];
