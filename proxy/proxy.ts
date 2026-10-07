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
    "X-DuckLake-Snapshot",
];

const FORWARDED_RESPONSE_HEADERS = [
    "Content-Type",
    "Content-Range",
    "Location",
    "Preference-Applied",
    "WWW-Authenticate",
    "X-Source",
    "X-DuckLake-Snapshot",
];

const CACHED_RESPONSE_HEADERS = ["X-Source", "X-DuckLake-Snapshot"];

const READ_METHODS = ["GET", "HEAD"];
const RETRY_STATUSES = [502, 503, 504];

const DEFAULT_MEDIA_TYPE = "application/json";
const JSON_TYPE = "application/json; charset=utf-8";
const DEFAULT_NO_CACHE_PATHS = ["/access_policy"];

const LOG_MESSAGES: Record<string, string> = {
    request: "Proxy request completed",
    "cache-read-failed": "Proxy cache read failed",
    "cache-write-rejected": "Proxy cache write was rejected",
    "cache-body-too-large": "Proxy cache write was skipped",
    "cache-write-failed": "Proxy cache write failed",
    "upstream-status": "Proxy upstream request returned an error status",
    "upstream-failed": "Proxy upstream request failed",
    "upstream-retried": "Proxy upstream request retry started",
};

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

interface CachedAnswer {
    body: string;
    headers: Record<string, string>;
}

interface RequestContext extends CacheKeyParts {
    upstream: string;
    retryUpstream: string;
    cacheTtl: string;
    maxBody: number;
    body: string;
    started: number;
    noCachePaths: string[];
}

interface SharedAnswer {
    response: ProxyResponse | null;
    source: string;
    leading: boolean;
}

interface LogFields {
    status?: number;
    source?: string;
    bytes?: number;
    error?: string;
}

type LogLevel = "info" | "warn";

interface SyncTable {
    name: string;
    cache_ttl?: number;
}

interface TableEntry {
    cacheTtl?: number;
}

interface SyncSchema {
    tables?: SyncTable[];
}

interface SyncConfig {
    schemas?: Record<string, SyncSchema>;
    noCachePaths?: string[];
}

interface ProxyConfig {
    tables: Record<string, TableEntry>;
    noCachePaths: string[];
}

declare const sync: SyncConfig | undefined;

const inFlight: Record<string, Promise<SharedAnswer>> = {};

let cachedSync: SyncConfig | undefined;
let cachedConfig: ProxyConfig = {
    tables: {},
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
    } catch (error) {
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
function upstreamUrl(upstream: string, ctx: RequestContext, path: string): string {
    return upstream + encodePath(path) + (ctx.args ? "?" + ctx.args : "");
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
        parts.headers["X-DuckLake-Snapshot"] || "",
    ]);

    return crypto.createHash("sha256").update(input).digest("hex");
}

/**
 * Builds the table lookup from the preloaded sync config.
 */
function buildTableMap(config: SyncConfig | undefined): Record<string, TableEntry> {
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
            map[parts[parts.length - 1]] = { cacheTtl: table.cache_ttl };
        }
    }

    return map;
}

/**
 * Builds the proxy config from the preloaded sync config.
 */
function buildProxyConfig(config: SyncConfig | undefined): ProxyConfig {
    return {
        tables: buildTableMap(config),
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
 * Returns the configured entry of the table in a request, if any.
 */
function tableFor(uri: string, map: Record<string, TableEntry>): TableEntry | null {
    const name = uri.split("?")[0].split("/")[1];

    return map[name] ?? null;
}

/**
 * Picks the upstream for a request. Single mode has one upstream. HA mode sends
 * reads to the read upstream and every other method to the write upstream.
 */
function upstreamFor(r: NginxHTTPRequest): string {
    if (r.variables.ha_mode === "true" && READ_METHODS.indexOf(r.method) === -1) {
        return r.variables.postgrest_write || "";
    }

    return r.variables.postgrest_read || "";
}

/**
 * Picks the upstream that serves a read again when the read upstream is down,
 * for example while the standbys start or after a failover. Only HA mode has
 * one, and a write is never retried.
 */
function retryUpstreamFor(r: NginxHTTPRequest): string {
    if (r.variables.ha_mode === "true" && READ_METHODS.indexOf(r.method) !== -1) {
        return r.variables.postgrest_write || "";
    }

    return "";
}

/**
 * Reads the values that the handler and the query helpers work with.
 */
function requestContext(
    r: NginxHTTPRequest,
    config: ProxyConfig,
): RequestContext {
    const jwt = decodeJWT(r.headersIn["Authorization"] || "");
    const upstream = upstreamFor(r);
    const profile = r.headersIn["Accept-Profile"] || "";
    const table = tableFor(r.uri, config.tables);
    const lifetime = r.variables.proxy_cache_ttl || "";

    return {
        method: r.method,
        uri: r.uri,
        args: r.variables.args || "",
        sub: jwt.sub,
        schemas: jwt.schemas,
        profile: profile,
        headers: buildHeaders(r),
        upstream: upstream,
        retryUpstream: retryUpstreamFor(r),
        cacheTtl: table?.cacheTtl ? String(table.cacheTtl) : lifetime,
        maxBody: Number(r.variables.proxy_max_body || "0"),
        body: r.requestText || "",
        started: Date.now(),
        noCachePaths: config.noCachePaths,
    };
}

/**
 * Writes one log line as `<Component> <action> <state>: <context>`.
 *
 * Every line carries the method, the path and the elapsed time.
 * The token and the query string are never part of a line.
 */
function log(
    r: NginxHTTPRequest,
    ctx: RequestContext,
    level: LogLevel,
    event: string,
    fields: LogFields,
): void {
    const context = [`method=${ctx.method}`, `path=${ctx.uri}`];

    if (fields.status !== undefined) {
        context.push(`status=${fields.status}`);
    }

    if (fields.source) {
        context.push(`source=${fields.source}`);
    }

    context.push(`duration_ms=${Date.now() - ctx.started}`);

    if (fields.bytes !== undefined) {
        context.push(`bytes=${fields.bytes}`);
    }

    if (fields.error) {
        context.push(`error=${fields.error}`);
    }

    const message = `${LOG_MESSAGES[event] || "Proxy request state changed"}: ${context.join(" ")}`;

    if (level === "warn") {
        r.warn(message);
        return;
    }

    r.log(message);
}

/**
 * Returns the cached answer for a key: the body and the headers that describe it.
 */
async function readCache(
    r: NginxHTTPRequest,
    ctx: RequestContext,
    key: string,
): Promise<CachedAnswer | null> {
    try {
        const res = await ngx.fetch(WEBDIS_READ + "/GET/" + key);

        if (res.status !== 200) {
            return null;
        }

        const text = await res.text();
        const data = JSON.parse(text);

        if (data.GET && typeof data.GET === "string") {
            const stored = JSON.parse(data.GET);

            if (stored && typeof stored.body === "string") {
                return { body: stored.body, headers: stored.headers || {} };
            }
        }
    } catch (e) {
        log(r, ctx, "warn", "cache-read-failed", { error: "cache-read" });
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
            error: "webdis-rejected",
        });
    } catch (e) {
        log(r, ctx, "warn", "cache-write-failed", { error: "cache-write" });
    }

    return false;
}

/**
 * Sends the request to one upstream.
 */
async function attemptUpstream(
    r: NginxHTTPRequest,
    ctx: RequestContext,
    upstream: string,
): Promise<ProxyResponse | null> {
    try {
        const res = await ngx.fetch(upstreamUrl(upstream, ctx, ctx.uri), {
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
        log(r, ctx, "warn", "upstream-failed", { error: "upstream-request" });
    }

    return null;
}

/**
 * Queries the PostgREST upstream. A read that finds its upstream unreachable or
 * without a healthy endpoint is asked once of the retry upstream.
 */
async function queryUpstream(
    r: NginxHTTPRequest,
    ctx: RequestContext,
): Promise<ProxyResponse | null> {
    const first = await attemptUpstream(r, ctx, ctx.upstream);

    if (
        !ctx.retryUpstream ||
        (first !== null && RETRY_STATUSES.indexOf(first.status) === -1)
    ) {
        return first;
    }

    log(r, ctx, "warn", "upstream-retried", { status: first?.status });

    return attemptUpstream(r, ctx, ctx.retryUpstream);
}

/**
 * Reads the cache, then queries the upstream when the cache has no answer.
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
                response: { status: 200, body: cached.body, headers: cached.headers },
                source: "cache",
                leading: true,
            };
        }
    }

    const response = await queryUpstream(r, ctx);

    if (response === null) {
        return { response: null, source: "none", leading: true };
    }

    return {
        response: response,
        source: "upstream",
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
 *
 * An empty answer is never stored: it can mean that access was denied, that the
 * data is not published yet, or that the policy changed, and each of those can
 * change before the entry expires.
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
        isEmpty(result.response.body) ||
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

    const headers: Record<string, string> = {};

    CACHED_RESPONSE_HEADERS.forEach((name) => {
        if (reply.headers[name]) {
            headers[name] = reply.headers[name];
        }
    });

    await writeCache(
        r,
        ctx,
        key,
        JSON.stringify({ body: reply.body, headers: headers }),
        ctx.cacheTtl,
    );
}

/**
 * Names where an answer came from for the log: the cache, or the sources that
 * PostgREST reports in `X-Source`.
 */
function loggedSource(result: SharedAnswer): string {
    if (result.source === "cache" || result.response === null) {
        return result.source;
    }

    return result.response.headers["X-Source"] || result.source;
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

    r.headersOut["X-Cache"] = result.source === "cache" ? "HIT" : "MISS";

    log(r, ctx, "info", "request", {
        status: reply.status,
        source: loggedSource(result),
        bytes: reply.body.length,
    });

    r.return(reply.status, reply.body);
}

/**
 * Serves one request: cache lookup, PostgREST query, and cache store.
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

            r.return(502, unavailable);
            return;
        }

        await maybeCache(r, ctx, key, result);
        sendResponse(r, ctx, result);
    } catch (error) {
        r.warn("Proxy request failed: error=proxy-handler");
        r.return(502, '{"error":"proxy exception"}');
    }
}

export default { handle };
