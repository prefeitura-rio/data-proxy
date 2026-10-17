export const API_URL = __ENV.BASE_URL || "http://istio-ingressgateway.istio-ingress.svc.cluster.local";
export const BIG_TABLE = "big_table";
export const CONFIGURE_DB_CRONJOB = __ENV.CONFIGURE_DB_CRONJOB || "data-proxy-configure-db";
export const E2E_SCRIPT_CONFIGMAP = __ENV.E2E_SCRIPT_CONFIGMAP || "data-proxy-e2e";
export const FAILING_TABLE = "failing_table";
export const FULL_TABLE = "full_table";
export const FULL_SOURCE = `rj-ia-desenvolvimento.dev.${FULL_TABLE}`;
export const HOST = __ENV.API_HOST || "data-proxy.local";
export const MAINTENANCE_CRONJOB = __ENV.MAINTENANCE_CRONJOB || "data-proxy-maintenance";
export const MIN_INSTANCES = 2;
export const MULTI_RLS_TABLE = "multi_rls_table";
export const NAMESPACE = __ENV.NAMESPACE || "data-proxy";
export const OIDC_CLIENT_SECRET = __ENV.OIDC_CLIENT_SECRET || "test-secret";
export const OIDC_NO_POLICY_CLIENT_ID = __ENV.OIDC_NO_POLICY_CLIENT_ID || "api_no_policy";
export const OIDC_USER_CLIENT_ID = __ENV.OIDC_USER_CLIENT_ID || "api";
export const PARTITIONED_SOURCE = __ENV.PARTITIONED_SOURCE || "rj-ia-desenvolvimento.dev.partitioned_table";
export const PARTITIONED_TABLE = "partitioned_table";
export const PARTITION_COLUMN = "date";
export const PG_IMAGE = __ENV.PG_IMAGE || "registry.localhost:5001/data-proxy-postgres:17.0.0-local";
export const PIPELINE = __ENV.PIPELINE || "data-proxy-sync";
export const CONSOLE_VIEWER_CLIENT_ID = __ENV.OIDC_CONSOLE_CLIENT_ID || "console";
export const CONSOLE_ADMIN_CLIENT_ID = __ENV.OIDC_ADMIN_CLIENT_ID || "admin";

/**
 * The client of the identity provider recipe: client roles and a user attribute, with users that log in by password.
 */
export const IDP_CLIENT_ID = __ENV.OIDC_IDP_CLIENT_ID || "idp";
export const IDP_PASSWORD = __ENV.OIDC_IDP_PASSWORD || "idp-test";
export const POLICY_CLIENT_ID = __ENV.OIDC_POLICY_WRITER_CLIENT_ID || "policy";
export const POLICY_REPLICATION_POLL_INTERVAL_SECONDS = Number(__ENV.POLICY_REPLICATION_POLL_INTERVAL_SECONDS || "2");
export const POLICY_REPLICATION_TIMEOUT_SECONDS = Number(__ENV.POLICY_REPLICATION_TIMEOUT_SECONDS || "120");
export const POLL_INTERVAL = 2;
export const POSTGREST_URL = __ENV.POSTGREST_URL || "http://data-proxy-postgrest.data-proxy.svc.cluster.local:3000";
export const SCHEMA = "test";
export const SNAPSHOT_SOURCE = __ENV.SNAPSHOT_SOURCE || "rj-ia-desenvolvimento.dev.snapshot_table";
export const SNAPSHOT_TABLE = "snapshot_table";
export const STATE_DATABASE = "DBOS_SYSTEM_DATABASE_URL";
export const SYNCED_PARTITIONS = 4;
export const TABLES = [FULL_TABLE, SNAPSHOT_TABLE, MULTI_RLS_TABLE, PARTITIONED_TABLE];
export const TOKEN_REFRESH_SECONDS = 60;

export const PROXY_ADMIN_URL =
    __ENV.PROXY_ADMIN_URL || "http://data-proxy-proxy-admin.data-proxy.svc.cluster.local:8081";

export const OIDC_TOKEN_URL =
    __ENV.OIDC_TOKEN_URL || "http://keycloak.keycloak.svc.cluster.local:8080/realms/dev/protocol/openid-connect/token";

export const CLUSTER_NAME = __ENV.CLUSTER_NAME || "data-proxy";
export const CLUSTER_SCALER = "data-proxy-cluster-autoscaler";
export const HEAVY_LIMIT_MIN = 1000;
export const LOAD_TABLES = ["full_table", "multi_rls_table", "partitioned_table", "big_table"];
export const LOAD_TOKEN_REFRESH_SECONDS = 30;
export const NEW_SCHEMA = "e2e_new";
export const NEW_SCHEMA_SOURCE = "rj-ia-desenvolvimento.dev.new_schema_table";
export const NEW_SCHEMA_UNIT_1_IDS = Array.from({ length: 10 }, (_, index) => 1 + 5 * index);
export const PARALLEL_READS = 10;
export const PAUSED_REPLICAS = "autoscaling.keda.sh/paused-replicas";
export const PGDOG = "data-proxy-pgdog";
export const SECRETS_MOUNT = "/var/lib/postgresql/data/.duckdb/stored_secrets";
export const SECRETS_SUBPATH = "duckdb/secrets";
export const SETUP_ATTEMPTS = 6;
export const SETUP_RETRY_SECONDS = 5;
export const VALKEY_POD = "data-proxy-valkey-0";

export const CONSOLE_VIEWS = [
    "schemas",
    "tables",
    "rls",
    "queues",
    "schedules",
    "runs",
    "tasks",
    "steps",
    "logs",
    "errors",
    "health",
    "synced",
    "overview",
];
