import http from "k6/http";
import { check, sleep } from "k6";

declare const __ENV: Record<string, string | undefined>;

type K6Response = {
  status: number;
  body: string;
  json: (path?: string) => unknown;
};
type FreshnessRow = {
  table: string;
  strategy: string;
  partition: string | null;
  status: string;
};

const API_URL = __ENV.BASE_URL || "http://istio-ingressgateway.istio-ingress.svc.cluster.local";
const OIDC_TOKEN_URL = __ENV.OIDC_TOKEN_URL || "http://mock-oauth2-server.data-proxy.svc.cluster.local:8080/default/token";
const OIDC_CLIENT_ID = __ENV.OIDC_CLIENT_ID || "dev";
const HOST = __ENV.API_HOST || "data-proxy.local";
const WEBDIS_URL = __ENV.WEBDIS_URL || "http://webdis.data-proxy.svc.cluster.local:7379";
const SCHEMA = "pic";
const POLL_INTERVAL = 0.5;
const MAX_DURATION = __ENV.MAX_DURATION || "10m";

export const options = {
  scenarios: {
    e2e: {
      executor: "constant-vus",
      vus: 1,
      duration: MAX_DURATION,
    },
  },
  thresholds: {
    checks: ["rate==1"],
  },
};

const FULL_TABLE = "endpoint_participante_listagem";
const MULTI_RLS_TABLE = "endpoint_participantes";
const PARTITIONED_TABLE = "protocolo_estado_diario";
const STREAMS = ["dp:extract", "dp:prepare", "dp:publish"];
const TABLES = [FULL_TABLE, MULTI_RLS_TABLE, PARTITIONED_TABLE];

function now(): string {
  return new Date().toISOString();
}

function log(stage: string, source: string, metric: string, value: unknown): void {
  console.log(JSON.stringify({ time: now(), stage, source, metric, value }));
}

function fetchToken(): string {
  const response = http.post(OIDC_TOKEN_URL, {
    grant_type: "client_credentials",
    client_id: OIDC_CLIENT_ID,
  }) as K6Response;
  const body = response.json() as { access_token?: string };
  if (!body.access_token) {
    throw new Error(`Token request failed: ${response.body}`);
  }
  return body.access_token;
}

function authHeaders(token: string): Record<string, string> {
  return {
    Authorization: `Bearer ${token}`,
    Host: HOST,
    "Accept-Profile": SCHEMA,
  };
}

interface MetricRequest {
  stage: string;
  source: string;
  metric: string;
  method: string;
  url: string;
  params: Record<string, unknown>;
  extract: (r: K6Response) => unknown;
}

function redisMetric(metric: string, command: string, extract: (r: K6Response) => unknown): MetricRequest {
  return {
    stage: "sync",
    source: "redis",
    metric,
    method: "GET",
    url: `${WEBDIS_URL}/${command}`,
    params: { tags: { name: `redis:${metric}` } },
    extract,
  };
}

function postgrestMetric(stage: string, source: string, metric: string, path: string, token: string, extract: (r: K6Response) => unknown): MetricRequest {
  return {
    stage,
    source,
    metric,
    method: "GET",
    url: `${API_URL}${path}`,
    params: { headers: authHeaders(token), tags: { name: metric } },
    extract,
  };
}

function buildMetrics(token: string): MetricRequest[] {
  const metrics: MetricRequest[] = [];

  for (const stream of STREAMS) {
    metrics.push(redisMetric(
      `stream_length:${stream}`,
      `XLEN/${stream}`,
      (r) => {
        if (r.status !== 200) return -1;
        const body = r.json() as { XLEN?: string };
        return Number(body?.XLEN ?? -1);
      },
    ));
  }

  metrics.push(redisMetric("db_size", "DBSIZE", (r) => {
    if (r.status !== 200) return -1;
    const body = r.json() as { DBSIZE?: string };
    return Number(body?.DBSIZE ?? -1);
  }));

  metrics.push(redisMetric("active_run", "GET/dp:active", (r) => {
    if (r.status !== 200) return null;
    const body = r.json() as { GET?: string | null };
    return body?.GET ?? null;
  }));

  for (const table of TABLES) {
    metrics.push(postgrestMetric("extract", "postgrest", `table_status:${table}`, `/${table}?limit=1`, token, (r) => r.status));
    metrics.push(postgrestMetric("publish", "postgrest", `table_row_count:${table}`, `/${table}?limit=1000`, token, (r) => {
      const body = r.json();
      return Array.isArray(body) ? body.length : 0;
    }));
  }

  for (const table of TABLES) {
    metrics.push(postgrestMetric("publish", "postgrest", `freshness_count:${table}`, `/freshness?table=eq.${table}`, token, (r) => {
      const rows = r.json() as FreshnessRow[];
      return Array.isArray(rows) ? rows.length : 0;
    }));
    metrics.push(postgrestMetric("publish", "postgrest", `freshness_all_success:${table}`, `/freshness?table=eq.${table}`, token, (r) => {
      const rows = r.json() as FreshnessRow[];
      return Array.isArray(rows) && rows.length > 0 && rows.every((row) => row.status === "success");
    }));
  }

  metrics.push(postgrestMetric("publish", "postgrest", `partition_count:${PARTITIONED_TABLE}`, `/freshness?table=eq.${PARTITIONED_TABLE}`, token, (r) => {
    const rows = r.json() as FreshnessRow[];
    if (!Array.isArray(rows)) return 0;
    return rows.filter((row) => row.partition !== null).length;
  }));

  return metrics;
}

function pollOnce(metrics: MetricRequest[]): boolean {
  const requests = metrics.map((m) => [m.method, m.url, m.params] as const);
  const responses = http.batch(requests) as K6Response[];

  let allStreamsDrained = true;
  let allTablesPopulated = true;
  let activeRunGone = true;

  for (let i = 0; i < metrics.length; i++) {
    const m = metrics[i];
    const value = m.extract(responses[i]);
    log(m.stage, m.source, m.metric, value);

    if (m.metric.startsWith("stream_length:") && typeof value === "number" && value !== 0) {
      allStreamsDrained = false;
    }
    if (m.metric === "active_run" && value !== null) {
      activeRunGone = false;
    }
    if (m.metric.startsWith("table_row_count:") && typeof value === "number" && value === 0) {
      allTablesPopulated = false;
    }
    if (m.metric.startsWith("freshness_count:") && typeof value === "number" && value === 0) {
      allTablesPopulated = false;
    }

    check(null, {
      [m.metric]: () => {
        if (typeof value === "boolean") return value;
        if (typeof value === "number") {
          if (m.metric.startsWith("stream_length:")) return value === 0;
          if (m.metric === "db_size") return value < 10;
          if (m.metric.startsWith("table_row_count:")) return value > 0;
          if (m.metric.startsWith("freshness_count:")) return value > 0;
          if (m.metric === "partition_count:" + PARTITIONED_TABLE) return value === 7;
          return true;
        }
        if (m.metric === "active_run") return value === null;
        return true;
      },
    });
  }

  return allStreamsDrained && allTablesPopulated && activeRunGone;
}

export default function (): void {
  const token = fetchToken();
  const metrics = buildMetrics(token);

  const deadline = Date.now() + 600_000;
  while (Date.now() < deadline) {
    const complete = pollOnce(metrics);
    if (complete) {
      log("done", "redis", "pipeline_complete", true);
      break;
    }
    sleep(POLL_INTERVAL);
  }
}
