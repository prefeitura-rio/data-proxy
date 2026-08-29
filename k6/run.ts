import http from "k6/http";
import { check, sleep } from "k6";

declare const __ENV: Record<string, string | undefined>;

type TokenData = { token: string; expiresAt: number };
type TokenResponse = { access_token?: string; expires_in?: number };
type K6Response = {
  status: number;
  body: string;
  json: (path?: string) => unknown;
};
type RequestOptions = {
  headers: Record<string, string>;
  tags: { name: string };
};
type Route = {
  profile: string;
  path: string;
  name: string;
  checkBody: (body: unknown) => boolean;
};

const API_URL = __ENV.BASE_URL || "http://istio-ingressgateway.istio-ingress.svc.cluster.local";
const OIDC_TOKEN_URL = __ENV.OIDC_TOKEN_URL || "http://mock-oauth2-server.data-proxy.svc.cluster.local:8080/default/token";
const OIDC_CLIENT_ID = __ENV.OIDC_CLIENT_ID || "dev";
const HOST = __ENV.API_HOST || "data-proxy.local";
const K6_PROFILE = __ENV.K6_PROFILE || "smoke";
const TOKEN_REFRESH_SECONDS = 30;

const PROFILE_OPTIONS = {
  smoke: { vus: 1, duration: "30s" },
  load: { vus: 10, duration: "5m" },
  stress: { vus: 50, duration: "10m" },
} as const;

if (!(K6_PROFILE in PROFILE_OPTIONS)) {
  throw new Error(`Unknown K6_PROFILE: ${K6_PROFILE}`);
}

const selectedProfile = PROFILE_OPTIONS[K6_PROFILE as keyof typeof PROFILE_OPTIONS];

export const options = {
  scenarios: {
    default: {
      executor: "constant-vus",
      vus: Number(__ENV.VUS || selectedProfile.vus),
      duration: __ENV.DURATION || selectedProfile.duration,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<1000"],
  },
};

const isArray = (body: unknown): boolean => Array.isArray(body);

const ROUTES: Route[] = [
  {
    profile: "pic",
    path: "/endpoint_participante_listagem?id_unidade=eq.cras_1&limit=20",
    name: "pic_endpoint_participante_listagem",
    checkBody: isArray,
  },
  {
    profile: "pic",
    path: "/freshness?table=eq.endpoint_participante_listagem",
    name: "pic_freshness",
    checkBody: isArray,
  },
  {
    profile: "pic",
    path: "/endpoint_participantes?id_cras=eq.cras_1&limit=20",
    name: "projeto_endpoint_participantes_cras",
    checkBody: isArray,
  },
  {
    profile: "pic",
    path: "/endpoint_participantes?id_escola=eq.escola_1&limit=20",
    name: "projeto_endpoint_participantes_escola",
    checkBody: isArray,
  },
  {
    profile: "pic",
    path: "/protocolo_estado_diario?id_unidade=eq.cras_1&limit=20&order=protocolo_data_referencia_particicao.desc",
    name: "projeto_protocolo_estado_diario",
    checkBody: isArray,
  },
  {
    profile: "pic",
    path: "/freshness?table=eq.protocolo_estado_diario",
    name: "projeto_freshness",
    checkBody: isArray,
  },
];

function fetchToken(): TokenData {
  const response = http.post(OIDC_TOKEN_URL, {
    grant_type: "client_credentials",
    client_id: OIDC_CLIENT_ID,
  }) as K6Response;
  const body = response.json() as TokenResponse;
  check(response, { "token request succeeded": (item: K6Response) => item.status === 200 });
  if (!body.access_token) {
    throw new Error(`Token request did not return access_token: ${response.body}`);
  }
  return {
    token: body.access_token,
    expiresAt: Date.now() + (body.expires_in || 300) * 1000,
  };
}

export function setup(): TokenData {
  return fetchToken();
}

let vuToken = "";
let vuExpiresAt = 0;

function ensureToken(data: TokenData): string {
  if (!vuToken || Date.now() >= vuExpiresAt - TOKEN_REFRESH_SECONDS * 1000) {
    const next = vuToken ? fetchToken() : data;
    vuToken = next.token;
    vuExpiresAt = next.expiresAt;
  }
  return vuToken;
}

function request(route: Route, token: string): void {
  const options: RequestOptions = {
    headers: {
      Authorization: `Bearer ${token}`,
      Host: HOST,
      "Accept-Profile": route.profile,
    },
    tags: { name: route.name },
  };
  const response = http.get(`${API_URL}${route.path}`, options) as K6Response;
  const body = response.json();
  check(response, {
    [`${route.name} returned 200`]: (item: K6Response) => item.status === 200,
    [`${route.name} returned JSON array`]: () => route.checkBody(body),
  });
}

export default function (data: TokenData): void {
  const route = ROUTES[Math.floor(Math.random() * ROUTES.length)];
  request(route, ensureToken(data));
  sleep(1);
}
