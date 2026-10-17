import {
    BIG_TABLE,
    consoleCall,
    FAILING_TABLE,
    FULL_TABLE,
    memberOf,
    MULTI_RLS_TABLE,
    PARTITIONED_SOURCE,
    PARTITIONED_TABLE,
    recordsOf,
    safeJson,
    scenario,
    SCHEMA,
    SNAPSHOT_TABLE,
    SYNCED_PARTITIONS,
} from "../lib/index.js";

const { requirePrecondition } = scenario("fixture");

/**
 * Verifies that the configuration of the console matches the k6 fixture contract.
 */
export function contract() {
    const response = consoleCall("GET", "/rpc/sync_config", "admin");

    requirePrecondition("the console serves the sync configuration", response.status === 200, {
        status: response.status,
        body: String(response.body).slice(0, 300),
    });

    const schema = memberOf(memberOf(safeJson(response), "schemas"), SCHEMA);
    const tables = recordsOf(schema.tables);
    const table = /** @param {string} name */ (name) => tables.find((item) => String(item.name).endsWith(`.${name}`));
    const partitioned = tables.find((item) => item.name === PARTITIONED_SOURCE);

    requirePrecondition("the sync fixture claims by preferred_username", schema.claim === "preferred_username", {});
    requirePrecondition("the sync fixture declares a source type", memberOf(schema, "source").type !== undefined, {});

    for (const name of [FULL_TABLE, SNAPSHOT_TABLE, MULTI_RLS_TABLE, PARTITIONED_TABLE, BIG_TABLE, FAILING_TABLE]) {
        requirePrecondition(`the sync fixture defines ${name}`, table(name) !== undefined, {});
    }

    requirePrecondition("the partitioned fixture is partitioned", partitioned?.strategy === "partitioned", {});
    requirePrecondition("the partitioned fixture has a fallback", partitioned?.fallback === true, {});
    requirePrecondition("the partitioned fixture keeps four partitions", partitioned?.n === SYNCED_PARTITIONS, {});
    requirePrecondition("the large fixture is partitioned", table(BIG_TABLE)?.strategy === "partitioned", {});
    requirePrecondition("the large fixture keeps thirty partitions", table(BIG_TABLE)?.n === 30, {});
}
