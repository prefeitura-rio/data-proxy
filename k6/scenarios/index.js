import * as access from "./access.js";
import * as cache from "./cache.js";
import * as cachestore from "./cachestore.js";
import * as cleanup from "./cleanup.js";
import * as consoleScenario from "./console.js";
import * as ducklake from "./ducklake.js";
import * as failover from "./failover.js";
import * as idp from "./idp.js";
import * as jobs from "./jobs.js";
import * as metrics from "./metrics.js";
import * as newschema from "./newschema.js";
import * as partitions from "./partitions.js";
import * as queryScope from "./query_scope.js";
import * as queries from "./queries.js";
import * as roles from "./roles.js";
import * as runs from "./runs.js";
import * as schedule from "./schedule.js";
import * as snapshots from "./snapshots.js";
import * as standby from "./standby.js";
import * as sync from "./sync.js";
import * as tokens from "./tokens.js";
import * as topology from "./topology.js";

/**
 * Every step of every scenario, in the order of its stage. A new scenario adds its import and its `steps` here.
 * @type {Step[]}
 */
export const STEPS = [
    ...access.steps,
    ...cache.steps,
    ...cachestore.steps,
    ...cleanup.steps,
    ...consoleScenario.steps,
    ...ducklake.steps,
    ...failover.steps,
    ...idp.steps,
    ...jobs.steps,
    ...metrics.steps,
    ...newschema.steps,
    ...partitions.steps,
    ...queries.steps,
    ...queryScope.steps,
    ...roles.steps,
    ...runs.steps,
    ...schedule.steps,
    ...snapshots.steps,
    ...standby.steps,
    ...sync.steps,
    ...tokens.steps,
    ...topology.steps,
].sort((a, b) => a.stage - b.stage);
