/// <reference path="../node_modules/njs-types/ngx_http_js_module.d.ts" />
import crypto from "crypto";

const WEBDIS_READ = "http://127.0.0.1:7380";
const WEBDIS_WRITE = "http://127.0.0.1:7379";

const FORWARDED_HEADERS = [
    "Authorization",
    "Accept-Profile",
    "Content-Profile",
    "Prefer",
    "Range",
    "Accept",
    "Content-Type",
];

const FORWARDED_RESPONSE_HEADERS = [
    "Content-Type",
    "Content-Range",
    "Location",
    "Preference-Applied",
    "WWW-Authenticate",
];

const DEFAULT_MEDIA_TYPE = "application/json";
const JSON_TYPE = "application/json; charset=utf-8";
const DEFAULT_NO_FALLBACK_PATHS = ["/access_policy"];
const DEFAULT_NO_CACHE_PATHS = ["/access_policy"];

interface CacheKeyParts {
    method: string;
    uri: string;
    args: string;
    sub: string;
    schemas: string;
    profile: string;
    headers: Record<string, string>;
}

interface ProxyResponse {
    status: number;
    body: string;
    headers: Record<string, string>;
}

interface RequestContext extends CacheKeyParts {
    upstream: string;
    cacheTtl: string;
    fallback: boolean;
    maxBody: number;
    body: string;
    started: number;
    noFallbackPaths: string[];
    noCachePaths: string[];
}

interface SharedAnswer {
    response: ProxyResponse | null;
    source: AnswerSource;
    leading: boolean;
}

interface LogFields {
    status?: number;
    source?: AnswerSource;
    bytes?: number;
    key?: string;
    error?: string;
    answer?: string;
}

type AnswerSource = "cache" | "parquet" | "bigquery" | "none";
type LogLevel = "info" | "warn";

interface SyncTable {
    name: string;
    fallback?: boolean;
    cache_ttl?: number;
}

interface TableEntry {
    fallback: boolean;
    cacheTtl?: number;
}

interface SyncSchema {
    tables?: SyncTable[];
}

interface SyncConfig {
    schemas?: Record<string, SyncSchema>;
    noFallbackPaths?: string[];
    noCachePaths?: string[];
}

interface ProxyConfig {
    fallbackMap: Record<string, TableEntry>;
    noFallbackPaths: string[];
    noCachePaths: string[];
}

interface FallbackResolver {
    source: AnswerSource;
    shouldTry(ctx: RequestContext, response: ProxyResponse): boolean;
    resolve(r: NginxHTTPRequest, ctx: RequestContext): Promise<ProxyResponse | null>;
}

declare const sync: SyncConfig | undefined;

const inFlight: Record<string, Promise<SharedAnswer>> = {};

const fallbackResolvers: FallbackResolver[] = [
    {
        source: "bigquery",
        shouldTry: (ctx, response) =>
            ctx.method === "GET" &&
            ctx.fallback &&
            response.status === 200 &&
            isEmpty(response.body),
        resolve: queryFallback,
    },
];

let cachedSync: SyncConfig | undefined;
let cachedConfig: ProxyConfig = {
    fallbackMap: {},
    noFallbackPaths: DEFAULT_NO_FALLBACK_PATHS,
    noCachePaths: DEFAULT_NO_CACHE_PATHS,
};

/**
 * Reports whether a URI matches any prefix in the given list.
 */
function matchesPrefix(uri: string, prefixes: string[]): boolean {
    return prefixes.some((p) => uri === p || uri.startsWith(p + "/"));
}

/**
 * Reports whether a PostgREST response carries no rows.
 */
function isEmpty(body: string): boolean {
    if (!body || body.trim() === "") {
        return true;
    }
    const t = body.trim();
    return t === "[]" || t === "null";
}

/**
 * Reduces an Accept header to the media type that decides the answer format.
 */
function normalizeAccept(value: string): string {
    if (!value) {
        return DEFAULT_MEDIA_TYPE;
    }

    const media = value.split(",")[0].split(";")[0].trim().toLowerCase();

    if (media === "" || media === "*/*") {
        return DEFAULT_MEDIA_TYPE;
    }

    return media;
}

/**
 * Reads the identity and the schemas claim from a bearer token.
 *
 * The signature is not verified here. The same Authorization header is sent to
 * PostgREST, which validates the token, so this function only reads the claims
 * that take part in the cache key.
 */
function decodeJWT(header: string): { sub: string; schemas: string } {
    if (!header || !header.startsWith("Bearer ")) {
        return { sub: "anon", schemas: "" };
    }

    try {
        const parts = header.substring(7).split(".");

        if (parts.length < 2) {
            return { sub: "anon", schemas: "" };
        }

        const claims = JSON.parse(
            Buffer.from(parts[1], "base64url").toString("utf8"),
        );
        const sub = claims.preferred_username || claims.sub || "anon";
        const schemas = Array.isArray(claims.schemas)
            ? claims.schemas.join(",")
            : claims.schemas || "";

        return { sub: sub, schemas: schemas };
    } catch {
        return { sub: "anon", schemas: "" };
    }
}

/**
 * Encodes a path for the upstream request.
 *
 * The nginx request URI arrives percent decoded, so a reserved character that
 * the client sent in encoded form would become literal and split the path from
 * the query string. Slashes are restored afterwards, so that they stay
 * separators.
 */
function encodePath(path: string): string {
    return encodeURIComponent(path).replace(/%2F/g, "/");
}

/**
 * Builds the URL of an upstream request.
 */
function upstreamUrl(ctx: RequestContext, path: string): string {
    return ctx.upstream + encodePath(path) + (ctx.args ? "?" + ctx.args : "");
}

/**
 * Reads one header from an upstream answer.
 *
 * The engine exposes the headers of a fetch answer in more than one shape, so
 * both a headers object with a getter and a plain object are accepted.
 */
function readHeader(raw: unknown, name: string): string {
    const source = raw as { get?: (key: string) => string | null } & Record<
        string,
        string
    >;

    if (typeof source.get === "function") {
        return source.get(name) || "";
    }

    return source[name] || source[name.toLowerCase()] || "";
}

/**
 * Copies the request headers that PostgREST needs onto the outgoing request.
 */
function buildHeaders(r: NginxHTTPRequest): Record<string, string> {
    const h: Record<string, string> = {};

    FORWARDED_HEADERS.forEach((name) => {
        const value = r.headersIn[name];
        if (value) {
            h[name] = value;
        }
    });

    return h;
}

/**
 * Copies the headers that a client must see from an upstream answer.
 */
function responseHeaders(res: unknown): Record<string, string> {
    const raw = (res as { headers?: unknown }).headers;
    const out: Record<string, string> = {};

    if (!raw) {
        return out;
    }

    FORWARDED_RESPONSE_HEADERS.forEach((name) => {
        const value = readHeader(raw, name);
        if (value) {
            out[name] = value;
        }
    });

    return out;
}

/**
 * Reports whether an answer may be stored.
 *
 * An answer that is not json would come back with the wrong media type from the
 * cache. Range responses are excluded by the caller, which checks the request
 * headers instead of relying on the upstream to echo them back.
 */
function cacheable(response: ProxyResponse): boolean {
    const type = (response.headers["Content-Type"] || "").toLowerCase();
    return type === "" || type.indexOf("json") !== -1;
}

/**
 * Builds the cache key of a request.
 *
 * The identity claims and the representation headers are part of the key, so
 * that two users, or two content negotiations, never share a cache entry.
 */
function hashKey(parts: CacheKeyParts): string {
    const input = JSON.stringify([
        parts.method,
        parts.uri,
        parts.args,
        parts.sub,
        parts.schemas,
        parts.profile,
        parts.headers["Range"] || "",
        parts.headers["Prefer"] || "",
        normalizeAccept(parts.headers["Accept"] || ""),
    ]);

    return crypto.createHash("sha256").update(input).digest("hex");
}

/**
 * Builds the fallback lookup from the preloaded sync config.
 */
function buildFallbackMap(config: SyncConfig | undefined): Record<string, TableEntry> {
    const map: Record<string, TableEntry> = {};

    if (!config || !config.schemas) {
        return map;
    }

    for (const schemaName in config.schemas) {
        const tables = config.schemas[schemaName].tables;
        if (!tables) {
            continue;
        }

        for (let i = 0; i < tables.length; i++) {
            const table = tables[i];
            const parts = table.name.split(".");
            map[parts[parts.length - 1]] = {
                fallback: table.fallback !== false,
                cacheTtl: table.cache_ttl,
            };
        }
    }

    return map;
}

/**
 * Builds the proxy config from the preloaded sync config.
 */
function buildProxyConfig(config: SyncConfig | undefined): ProxyConfig {
    return {
        fallbackMap: buildFallbackMap(config),
        noFallbackPaths: (config && config.noFallbackPaths) || DEFAULT_NO_FALLBACK_PATHS,
        noCachePaths: (config && config.noCachePaths) || DEFAULT_NO_CACHE_PATHS,
    };
}

/**
 * Returns the proxy config, rebuilding it only when the sync reference changes.
 */
function proxyConfig(): ProxyConfig {
    const current = typeof sync !== "undefined" ? sync : undefined;

    if (current === cachedSync) {
        return cachedConfig;
    }

    cachedSync = current;
    cachedConfig = buildProxyConfig(current);
    return cachedConfig;
}

/**
 * Returns whether the table in a request has BigQuery fallback enabled.
 */
function tableFor(
    uri: string,
    map: Record<string, TableEntry>,
    noFallbackPaths: string[],
): TableEntry | null {
    if (matchesPrefix(uri, noFallbackPaths)) {
        return null;
    }

    const name = uri.split("?")[0].split("/")[1];

    return map[name] ?? null;
}

/**
 * Reads the values that the handler and the query helpers work with.
 */
function requestContext(
    r: NginxHTTPRequest,
    config: ProxyConfig,
): RequestContext {
    const jwt = decodeJWT(r.headersIn["Authorization"] || "");
    const upstream = r.variables.postgrest_read || "";
    const profile = r.headersIn["Accept-Profile"] || "";
    const table = tableFor(r.uri, config.fallbackMap, config.noFallbackPaths);
    const lifetime = r.variables.fallback_cache_ttl || "";

    return {
        method: r.method,
        uri: r.uri,
        args: r.variables.args || "",
        sub: jwt.sub,
        schemas: jwt.schemas,
        profile: profile,
        headers: buildHeaders(r),
        upstream: upstream,
        cacheTtl: table?.cacheTtl ? String(table.cacheTtl) : lifetime,
        fallback: table?.fallback ?? false,
        maxBody: Number(r.variables.fallback_max_body || "0"),
        body: r.requestText || "",
        started: Date.now(),
        noFallbackPaths: config.noFallbackPaths,
        noCachePaths: config.noCachePaths,
    };
}

/**
 * Writes one log line as JSON.
 *
 * Every line carries the event, the method, the path and the elapsed time.
 * The token and the query string are never part of a line.
 */
function log(
    r: NginxHTTPRequest,
    ctx: RequestContext,
    level: LogLevel,
    event: string,
    fields: LogFields,
): void {
    const line = JSON.stringify({
        event: event,
        method: ctx.method,
        uri: ctx.uri,
        status: fields.status,
        source: fields.source,
        wait_ms: Date.now() - ctx.started,
        bytes: fields.bytes,
        key: fields.key,
        error: fields.error,
        answer: fields.answer,
    });

    if (level === "warn") {
        r.warn(line);
        return;
    }

    r.log(line);
}

/**
 * Returns the cached body for a key.
 */
async function readCache(
    r: NginxHTTPRequest,
    ctx: RequestContext,
    key: string,
): Promise<string | null> {
    try {
        const res = await ngx.fetch(WEBDIS_READ + "/GET/" + key);

        if (res.status !== 200) {
            return null;
        }

        const text = await res.text();
        const data = JSON.parse(text);

        if (data.GET && typeof data.GET === "string") {
            return data.GET;
        }
    } catch (e) {
        log(r, ctx, "warn", "cache-read-failed", { key: key, error: String(e) });
    }

    return null;
}

/**
 * Stores a response body in the cache under a key with the given TTL.
 *
 * Webdis answers with a success status even when the store is rejected, and
 * reports the outcome in the first element of the answer. The body is read so
 * that a rejected store is reported instead of counted as stored. The command
 * travels in the body of the request, because a large response body in the URL
 * would exceed the request line limit.
 */
async function writeCache(
    r: NginxHTTPRequest,
    ctx: RequestContext,
    key: string,
    body: string,
    ttl: string,
): Promise<boolean> {
    try {
        const encoded = encodeURIComponent(body);
        const res = await ngx.fetch(WEBDIS_WRITE + "/", {
            method: "POST",
            body: "SETEX/" + key + "/" + ttl + "/" + encoded,
        });
        const text = await res.text();
        const answer = JSON.parse(text).SETEX;

        if (answer && answer[0] === true) {
            return true;
        }

        log(r, ctx, "warn", "cache-write-rejected", {
            key: key,
            answer: text.substring(0, 200),
        });
    } catch (e) {
        log(r, ctx, "warn", "cache-write-failed", { key: key, error: String(e) });
    }

    return false;
}

/**
 * Queries the local PostgREST upstream.
 */
async function queryUpstream(
    r: NginxHTTPRequest,
    ctx: RequestContext,
): Promise<ProxyResponse | null> {
    try {
        const res = await ngx.fetch(upstreamUrl(ctx, ctx.uri), {
            method: ctx.method,
            headers: ctx.headers,
            body: ctx.body || undefined,
        });

        const body = await res.text();

        if (res.status >= 500) {
            log(r, ctx, "warn", "upstream-status", { status: res.status });
        }

        return { status: res.status, body: body, headers: responseHeaders(res) };
    } catch (e) {
        log(r, ctx, "warn", "upstream-failed", { error: String(e) });
    }

    return null;
}

/**
 * Queries the BigQuery endpoint that backs the table.
 */
async function queryFallback(
    r: NginxHTTPRequest,
    ctx: RequestContext,
): Promise<ProxyResponse | null> {
    try {
        const res = await ngx.fetch(upstreamUrl(ctx, ctx.uri + "_bq"), {
            method: "GET",
            headers: ctx.headers,
        });

        if (res.status !== 200) {
            log(r, ctx, "warn", "fallback-status", { status: res.status });
            return null;
        }

        const body = await res.text();

        if (isEmpty(body)) {
            return null;
        }

        return { status: 200, body: body, headers: responseHeaders(res) };
    } catch (e) {
        log(r, ctx, "warn", "fallback-failed", { error: String(e) });
    }

    return null;
}

/**
 * Reads the cache, queries the upstream, and escalates through the fallback
 * resolver chain when the upstream answer is empty.
 */
async function fetchAnswer(
    r: NginxHTTPRequest,
    ctx: RequestContext,
    key: string,
): Promise<SharedAnswer> {
    if (ctx.method === "GET" && !matchesPrefix(ctx.uri, ctx.noCachePaths)) {
        const cached = await readCache(r, ctx, key);

        if (cached !== null) {
            return {
                response: { status: 200, body: cached, headers: {} },
                source: "cache",
                leading: true,
            };
        }
    }

    const response = await queryUpstream(r, ctx);

    if (response === null) {
        return { response: null, source: "none", leading: true };
    }

    for (let i = 0; i < fallbackResolvers.length; i++) {
        const resolver = fallbackResolvers[i];
        if (resolver.shouldTry(ctx, response)) {
            const fallback = await resolver.resolve(r, ctx);
            if (fallback !== null) {
                return { response: fallback, source: resolver.source, leading: true };
            }
        }
    }

    return {
        response: response,
        source: "parquet",
        leading: true,
    };
}

/**
 * Answers one request, sharing the call with the requests that ask for the same
 * key at the same time, so that a burst reaches the upstream once.
 */
async function answer(
    r: NginxHTTPRequest,
    ctx: RequestContext,
    key: string,
): Promise<SharedAnswer> {
    const pending = inFlight[key];

    if (pending) {
        const joined = await pending;
        return { response: joined.response, source: joined.source, leading: false };
    }

    const promise = fetchAnswer(r, ctx, key);

    inFlight[key] = promise;

    try {
        const mine = await promise;
        return { response: mine.response, source: mine.source, leading: true };
    } finally {
        delete inFlight[key];
    }
}

/**
 * Writes the response to the cache when it is cacheable.
 */
async function maybeCache(
    r: NginxHTTPRequest,
    ctx: RequestContext,
    key: string,
    result: SharedAnswer,
): Promise<void> {
    if (
        !result.leading ||
        result.source === "cache" ||
        ctx.method !== "GET" ||
        matchesPrefix(ctx.uri, ctx.noCachePaths) ||
        result.response === null ||
        result.response.status !== 200 ||
        !!ctx.headers["Range"] ||
        !cacheable(result.response)
    ) {
        return;
    }

    const reply = result.response;

    if (ctx.maxBody > 0 && reply.body.length > ctx.maxBody) {
        log(r, ctx, "warn", "cache-body-too-large", {
            bytes: reply.body.length,
        });
        return;
    }

    const ttl = isEmpty(reply.body)
        ? (r.variables.empty_cache_ttl || "3600")
        : ctx.cacheTtl;

    await writeCache(r, ctx, key, reply.body, ttl);
}

/**
 * Sends the response to the client with the appropriate headers.
 */
function sendResponse(
    r: NginxHTTPRequest,
    ctx: RequestContext,
    result: SharedAnswer,
): void {
    const reply = result.response as ProxyResponse;

    Object.keys(reply.headers).forEach((name) => {
        r.headersOut[name] = reply.headers[name];
    });

    if (!r.headersOut["Content-Type"]) {
        r.headersOut["Content-Type"] = JSON_TYPE;
    }

    r.headersOut["X-Source"] = result.source;
    r.headersOut["X-Cache"] = result.source === "cache" ? "HIT" : "MISS";

    log(r, ctx, "info", "request", {
        status: reply.status,
        source: result.source,
        bytes: reply.body.length,
    });

    r.return(reply.status, reply.body);
}

/**
 * Serves one request: cache lookup, local PostgREST query, fallback resolvers
 * and cache store.
 */
async function handle(r: NginxHTTPRequest): Promise<void> {
    const config = proxyConfig();

    try {
        const ctx = requestContext(r, config);
        const key = hashKey(ctx);
        const result = await answer(r, ctx, key);

        if (result.response === null) {
            const unavailable = '{"error":"PostgREST unavailable"}';

            log(r, ctx, "info", "request", {
                status: 502,
                source: "none",
                bytes: unavailable.length,
            });

            r.headersOut["X-Source"] = "none";
            r.return(502, unavailable);
            return;
        }

        await maybeCache(r, ctx, key, result);
        sendResponse(r, ctx, result);
    } catch (e) {
        const error = e as { stack?: string };
        r.warn(
            JSON.stringify({
                event: "exception",
                error: String(e),
                stack: error.stack,
            }),
        );
        r.return(502, '{"error":"proxy exception"}');
    }
}

export default { handle };
