/// <reference path="types/ngx_http_js_module.d.ts" />

// fallback.ts — nginx njs proxy with Redis cache + BigQuery fallback

import crypto from 'crypto';

/** Webdis cache endpoint on the sidecar container. */
const WEBDIS = 'http://127.0.0.1:7379';

/** Headers forwarded to PostgREST, in this order; absent or empty ones are skipped. */
const FORWARDED_HEADERS = [
    'Authorization', 'Accept-Profile', 'Content-Profile', 'Prefer', 'Range', 'Accept', 'Content-Type',
];

/** Media type PostgREST answers with when the request names no format. */
const DEFAULT_MEDIA_TYPE = 'application/json';

/** Headers copied from the upstream answer onto the client answer. */
const FORWARDED_RESPONSE_HEADERS = [
    'Content-Type', 'Content-Range', 'Location', 'Preference-Applied', 'WWW-Authenticate',
];

/** Media type the proxy reports when the upstream names none. */
const JSON_TYPE = 'application/json; charset=utf-8';

/** Paths that never have a BigQuery view, so the fallback skips them. */
const NO_FALLBACK_PATHS = ['/freshness', '/access_policy'];

/** One table of the sync configuration, as the preloaded file writes it. */
interface SyncTable {
    name: string;
    fallback?: boolean;
    cache_ttl?: number;
}

/** One schema of the sync configuration. */
interface SyncSchema {
    tables?: SyncTable[];
}

/** The sync configuration, preloaded by the nginx configuration. */
interface SyncConfig {
    schemas?: Record<string, SyncSchema>;
}

declare const sync: SyncConfig | undefined;

/** Reports whether a path is one that the views never cover. */
function skipsFallback(uri: string): boolean {
    for (var i = 0; i < NO_FALLBACK_PATHS.length; i++) {
        if (uri === NO_FALLBACK_PATHS[i] || uri.startsWith(NO_FALLBACK_PATHS[i] + '/')) {
            return true;
        }
    }

    return false;
}

/** Reports whether one schema holds a table with this name, and returns it. */
function findTable(schema: SyncSchema, name: string): SyncTable | null {
    if (!schema.tables) { return null; }

    for (var i = 0; i < schema.tables.length; i++) {
        var table = schema.tables[i];
        var parts = table.name.split('.');

        if (parts[parts.length - 1] === name) { return table; }
    }

    return null;
}

/**
 * Returns the configured table of a request, or null when it has none.
 *
 * @param uri - Request path, without the query string.
 * @param profile - Schema named by the Accept-Profile header, empty when absent.
 * @returns The table entry, or null for an endpoint that the views never cover,
 *   for a schema that is not configured and for a config that is not mounted.
 */
function tableFor(uri: string, profile: string): SyncTable | null {
    if (skipsFallback(uri)) { return null; }

    if (typeof sync === 'undefined' || !sync.schemas) { return null; }

    var schemas = sync.schemas;
    var parts = uri.split('?')[0].split('/');
    var name = parts[1];

    if (profile) {
        return profile in schemas ? findTable(schemas[profile], name) : null;
    }

    var keys = Object.keys(schemas);

    for (var i = 0; i < keys.length; i++) {
        var found = findTable(schemas[keys[i]], name);

        if (found) { return found; }
    }

    return null;
}

/**
 * Reduces an Accept header to the media type that decides the answer format.
 *
 * @param value - Raw Accept header, empty when the request carries none.
 * @returns The first media type without its parameters, or the default type.
 */
function normalizeAccept(value: string): string {
    if (!value) { return DEFAULT_MEDIA_TYPE; }

    var media = value.split(',')[0].split(';')[0].trim().toLowerCase();

    if (media === '' || media === '*/*') { return DEFAULT_MEDIA_TYPE; }

    return media;
}

/** The values that take part in the cache key of a request. */
interface CacheKeyParts {
    method: string;
    uri: string;
    args: string;
    sub: string;
    schemas: string;
    profile: string;
    headers: Record<string, string>;
}

/**
 * The status and body that the proxy returns to the client.
 */
interface ProxyResponse {
    status: number;
    body: string;
    headers: Record<string, string>;
}

/**
 * The request values that the handler reads from the nginx request and passes
 * to the query helpers.
 */
interface RequestContext extends CacheKeyParts {
    upstream: string;
    cacheTtl: string;
    fallback: boolean;
    maxBody: number;
    body: string;
    started: number;
}

/** Where the answer of a request came from. */
type AnswerSource = 'cache' | 'postgrest' | 'bigquery' | 'none';

/** Cache outcome of a request. */
type CacheOutcome = 'hit' | 'stored' | 'none';

/** Level of a log line. */
type LogLevel = 'info' | 'warn';

/**
 * The fields of a log line that are not shared by every event. A field that a
 * caller leaves out is dropped from the line.
 */
interface LogFields {
    status?: number;
    source?: AnswerSource;
    cache?: CacheOutcome;
    bytes?: number;
    key?: string;
    error?: string;
    answer?: string;
}

/**
 * Reads the identity and the schemas claim from a bearer token.
 *
 * The signature is not verified here. The same Authorization header is sent to
 * PostgREST, which validates the token, so this function only reads the claims
 * that take part in the cache key.
 *
 * @param header - Raw Authorization header value, for example "Bearer eyJ...".
 * @returns The subject and the comma separated schemas claim, or the anonymous
 *   identity when the header is missing, malformed or not a JSON payload.
 */
function decodeJWT(header: string): { sub: string, schemas: string } {
    if (!header || !header.startsWith('Bearer ')) {
        return { sub: 'anon', schemas: '' };
    }

    try {
        var parts = header.substring(7).split('.');

        if (parts.length < 2) {
            return { sub: 'anon', schemas: '' };
        }

        var payload = parts[1];
        var claims = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
        var sub = claims.preferred_username || claims.sub || 'anon';
        var schemas = claims.schemas || '';

        if (Array.isArray(schemas)) { schemas = schemas.join(','); }

        return { sub: sub, schemas: schemas };
    } catch (e) {
        return { sub: 'anon', schemas: '' };
    }
}

/**
 * Reports whether a PostgREST response carries no rows.
 *
 * @param body - Response body to inspect.
 * @returns True for a body that is empty or only whitespace, for "[]" and for
 *   "null", false otherwise.
 */
function isEmpty(body: string): boolean {
    if (!body || body.trim() === '') { return true; }
    var t = body.trim();
    return t === '[]' || t === 'null';
}

/**
 * Builds the cache key of a request.
 *
 * The identity claims and the representation headers are part of the key, so
 * that two users, or two content negotiations, never share a cache entry.
 *
 * @param parts - Request method, path, query string and identity claims, plus
 *   the headers that are forwarded to PostgREST.
 * @returns The sha256 hex digest of the JSON encoded tuple of the values.
 */
function hashKey(parts: CacheKeyParts): string {
    var input = JSON.stringify([
        parts.method, parts.uri, parts.args, parts.sub, parts.schemas, parts.profile,
        parts.headers['Range'] || '', parts.headers['Prefer'] || '',
        normalizeAccept(parts.headers['Accept'] || ''),
    ]);

    return crypto.createHash('sha256').update(input).digest('hex');
}

/**
 * Copies the request headers that PostgREST needs onto the outgoing request.
 *
 * @param r - Current request.
 * @returns One entry per entry of FORWARDED_HEADERS that is present on the
 *   incoming request.
 */
function buildHeaders(r: NginxHTTPRequest): Record<string, string> {
    var h: Record<string, string> = {};

    FORWARDED_HEADERS.forEach((name) => {
        var value = r.headersIn[name];
        if (value) { h[name] = value; }
    });

    return h;
}

/**
 * Reads the values that the handler and the query helpers work with.
 *
 * @param r - Current request.
 * @returns The cache key parts, including the decoded token claims, plus the
 *   upstream address, the cache lifetime and the arrival timestamp.
 */
function requestContext(r: NginxHTTPRequest): RequestContext {
    var jwt = decodeJWT(r.headersIn['Authorization'] || '');
    var profile = r.headersIn['Accept-Profile'] || '';
    var table = tableFor(r.uri, profile);
    var lifetime = r.variables.fallback_cache_ttl || '';

    return {
        method: r.method,
        uri: r.uri,
        args: r.variables.args || '',
        sub: jwt.sub,
        schemas: jwt.schemas,
        profile: profile,
        headers: buildHeaders(r),
        upstream: r.variables.fallback_pgrst || '',
        cacheTtl: table && table.cache_ttl ? String(table.cache_ttl) : lifetime,
        fallback: table !== null && table.fallback !== false,
        maxBody: Number(r.variables.fallback_max_body || '0'),
        body: r.requestText || '',
        started: Date.now(),
    };
}

/**
 * Writes one log line as JSON.
 *
 * Every line carries the event, the method, the path and the elapsed time.
 * The token and the query string are never part of a line.
 *
 * @param r - Current request.
 * @param ctx - Request values, used for the method, the path and the duration.
 * @param level - Info for a normal record, warn for a failure.
 * @param event - Name of the event, used to tell the lines apart.
 * @param fields - Fields of this event; a field left out is not written.
 */
function log(
    r: NginxHTTPRequest, ctx: RequestContext, level: LogLevel, event: string, fields: LogFields
): void {
    var line = JSON.stringify({
        event: event,
        method: ctx.method,
        uri: ctx.uri,
        status: fields.status,
        source: fields.source,
        cache: fields.cache,
        wait: Date.now() - ctx.started,
        bytes: fields.bytes,
        key: fields.key,
        error: fields.error,
        answer: fields.answer,
    });

    if (level === 'warn') {
        r.warn(line);
        return;
    }

    r.log(line);
}

/**
 * Returns the cached body for a key.
 *
 * @param r - Current request, used to report a cache failure.
 * @param ctx - Request values, used to report the path.
 * @param key - Cache key of the request.
 * @returns The cached body, or null when there is no usable entry.
 */
async function readCache(
    r: NginxHTTPRequest, ctx: RequestContext, key: string
): Promise<string | null> {
    try {
        var res = await ngx.fetch(WEBDIS + '/GET/' + key);

        if (res.status !== 200) { return null; }

        var text = await res.text();
        var data = JSON.parse(text);

        if (data.GET && typeof data.GET === 'string') { return data.GET; }
    } catch (e) {
        log(r, ctx, 'warn', 'cache-read-failed', { key: key, error: String(e) });
    }

    return null;
}

/**
 * Encodes a path for the upstream request.
 *
 * The nginx request URI arrives percent decoded, so a reserved character that
 * the client sent in encoded form would become literal and split the path from
 * the query string. Slashes are restored afterwards, so that they stay
 * separators.
 *
 * @param path - Path as read from the request.
 * @returns The path with every unsafe character escaped.
 */
function encodePath(path: string): string {
    return encodeURIComponent(path).replace(/%2F/g, '/');
}

/**
 * Builds the URL of an upstream request.
 *
 * The path is encoded, so that it cannot absorb the query string. Build the
 * path first when a suffix is needed, and pass it as one argument.
 *
 * @param ctx - Request values, with the upstream address and the query string.
 * @param path - Path to request, without the query string.
 * @returns The absolute URL of the upstream request.
 */
function upstreamUrl(ctx: RequestContext, path: string): string {
    return ctx.upstream + encodePath(path) + (ctx.args ? '?' + ctx.args : '');
}

/**
 * Reads one header from an upstream answer.
 *
 * The engine exposes the headers of a fetch answer in more than one shape, so
 * both a headers object with a getter and a plain object are accepted.
 *
 * @param raw - Headers of the upstream answer.
 * @param name - Header name to read, in its canonical spelling.
 * @returns The header value, or an empty string when it is absent.
 */
function readHeader(raw: unknown, name: string): string {
    var source = raw as { get?: (key: string) => string | null } & Record<string, string>;

    if (typeof source.get === 'function') { return source.get(name) || ''; }

    return source[name] || source[name.toLowerCase()] || '';
}

/**
 * Copies the headers that a client must see from an upstream answer.
 *
 * @param res - Upstream answer as returned by the engine.
 * @returns One entry per header of the allowlist that the answer carries.
 */
function responseHeaders(res: unknown): Record<string, string> {
    var raw = (res as { headers?: unknown }).headers;
    var out: Record<string, string> = {};

    if (!raw) { return out; }

    FORWARDED_RESPONSE_HEADERS.forEach((name) => {
        var value = readHeader(raw, name);

        if (value) { out[name] = value; }
    });

    return out;
}

/**
 * Reports whether an answer may be stored.
 *
 * An answer that is not json would come back with the wrong media type from the
 * cache. Range and location responses are excluded by the caller, which checks
 * the request headers instead of relying on the upstream to echo them back.
 *
 * @param response - Answer to inspect.
 * @returns True when the answer may be stored.
 */
function cacheable(response: ProxyResponse): boolean {
    var type = (response.headers['Content-Type'] || '').toLowerCase();

    return type === '' || type.indexOf('json') !== -1;
}

/**
 * Queries the local PostgREST upstream.
 *
 * @param r - Current request, used to report a query failure.
 * @param ctx - Request values, with the upstream address, path, query string,
 *   method and forwarded headers.
 * @returns The upstream status, body and headers, or null when the call failed.
 */
async function queryUpstream(r: NginxHTTPRequest, ctx: RequestContext): Promise<ProxyResponse | null> {
    try {
        var res = await ngx.fetch(upstreamUrl(ctx, ctx.uri), {
            method: ctx.method,
            headers: ctx.headers,
            body: ctx.body || undefined,
        });

        var body = await res.text();

        if (res.status >= 500) {
            log(r, ctx, 'warn', 'upstream-status', { status: res.status });
        }

        return { status: res.status, body: body, headers: responseHeaders(res) };
    } catch (e) {
        log(r, ctx, 'warn', 'upstream-failed', { error: String(e) });
    }

    return null;
}

/**
 * Queries the BigQuery endpoint that backs the table.
 *
 * @param r - Current request, used to report a query failure.
 * @param ctx - Request values, with the upstream address, path, query string
 *   and forwarded headers.
 * @returns A result that carries rows, or null when the query failed or the
 *   table has no rows in BigQuery either.
 */
async function queryFallback(r: NginxHTTPRequest, ctx: RequestContext): Promise<ProxyResponse | null> {
    try {
        var res = await ngx.fetch(upstreamUrl(ctx, ctx.uri + '_bq'), {
            method: 'GET',
            headers: ctx.headers,
        });

        if (res.status !== 200) {
            log(r, ctx, 'warn', 'fallback-status', { status: res.status });
            return null;
        }

        var body = await res.text();

        if (isEmpty(body)) { return null; }

        return { status: 200, body: body, headers: responseHeaders(res) };
    } catch (e) {
        log(r, ctx, 'warn', 'fallback-failed', { error: String(e) });
    }

    return null;
}

/**
 * Stores a response body in the cache under a key.
 *
 * Webdis answers with a success status even when the store is rejected, and
 * reports the outcome in the first element of the answer. The body is read so
 * that a rejected store is reported instead of counted as stored. The command
 * travels in the body of the request, because a large response body in the URL
 * would exceed the request line limit.
 *
 * @param r - Current request, used to report a store failure.
 * @param ctx - Request values, used to report the path and the cache lifetime.
 * @param key - Cache key of the request.
 * @param body - Response body to store.
 * @returns A promise for whether the body was stored.
 */
async function writeCache(
    r: NginxHTTPRequest, ctx: RequestContext, key: string, body: string
): Promise<boolean> {
    try {
        var encoded = encodeURIComponent(body);
        var res = await ngx.fetch(WEBDIS + '/', {
            method: 'POST',
            body: 'SETEX/' + key + '/' + ctx.cacheTtl + '/' + encoded,
        });
        var text = await res.text();
        var answer = JSON.parse(text).SETEX;

        if (answer && answer[0] === true) { return true; }

        log(r, ctx, 'warn', 'cache-write-rejected', { key: key, answer: text.substring(0, 200) });
    } catch (e) {
        log(r, ctx, 'warn', 'cache-write-failed', { key: key, error: String(e) });
    }

    return false;
}

/** One answered request, shared with the requests that wait for the same key. */
interface SharedAnswer {
    response: ProxyResponse | null;
    source: AnswerSource;
    leading: boolean;
}

/** Answers being fetched right now, keyed by cache key. */
var inFlight: Record<string, Promise<SharedAnswer>> = {};

/**
 * Reads the cache, queries the upstream and escalates to the BigQuery view when
 * the answer is empty.
 *
 * @param r - Current request, used to report failures.
 * @param ctx - Request values, with the upstream address and the fallback flag.
 * @param key - Cache key of the request.
 * @returns The answer and the source it came from.
 */
async function fetchAnswer(
    r: NginxHTTPRequest, ctx: RequestContext, key: string
): Promise<SharedAnswer> {
    if (ctx.method === 'GET') {
        var cached = await readCache(r, ctx, key);

        if (cached !== null) {
            return { response: { status: 200, body: cached, headers: {} }, source: 'cache', leading: true };
        }
    }

    var response = await queryUpstream(r, ctx);

    if (response === null) { return { response: null, source: 'none', leading: true }; }

    if (ctx.method === 'GET' && ctx.fallback && response.status === 200 && isEmpty(response.body)) {
        var fallback = await queryFallback(r, ctx);

        if (fallback !== null) { return { response: fallback, source: 'bigquery', leading: true }; }
    }

    return { response: response, source: 'postgrest', leading: true };
}

/**
 * Answers one request, sharing the call with the requests that ask for the same
 * key at the same time, so that a burst reaches the upstream once.
 *
 * @param r - Current request, used to report failures.
 * @param ctx - Request values of the request that asks first.
 * @param key - Cache key of the request.
 * @returns The answer and whether this request ran the call.
 */
async function answer(r: NginxHTTPRequest, ctx: RequestContext, key: string): Promise<SharedAnswer> {
    var pending = inFlight[key];

    if (pending) {
        var joined = await pending;

        return { response: joined.response, source: joined.source, leading: false };
    }

    var promise = fetchAnswer(r, ctx, key);

    inFlight[key] = promise;

    try {
        var mine = await promise;

        return { response: mine.response, source: mine.source, leading: true };
    } finally {
        delete inFlight[key];
    }
}

/**
 * Serves one request: cache lookup, local PostgREST query, BigQuery fallback
 * and cache store.
 *
 * @param r - Current request. The upstream address, the cache lifetime, the
 *   fallback flag and the largest stored body come from nginx variables.
 * @returns A promise that settles once the response has been sent.
 */
async function handle(r: NginxHTTPRequest): Promise<void> {
    var ctx = requestContext(r);
    var key = hashKey(ctx);
    var result = await answer(r, ctx, key);

    if (result.response === null) {
        var unavailable = '{"error":"PostgREST unavailable"}';

        log(r, ctx, 'info', 'request', {
            status: 502, source: 'none', cache: 'none', bytes: unavailable.length,
        });

        r.return(502, unavailable);
        return;
    }

    var reply = result.response;
    var cache: CacheOutcome = 'none';

    if (result.source === 'cache') {
        cache = 'hit';
    } else if (result.leading && ctx.method === 'GET' && reply.status === 200
        && !ctx.headers['Range'] && !isEmpty(reply.body) && cacheable(reply)) {
        if (ctx.maxBody > 0 && reply.body.length > ctx.maxBody) {
            log(r, ctx, 'warn', 'cache-body-too-large', { bytes: reply.body.length });
        } else {
            var stored = await writeCache(r, ctx, key, reply.body);

            if (stored) { cache = 'stored'; }
        }
    }

    Object.keys(reply.headers).forEach((name) => {
        r.headersOut[name] = reply.headers[name];
    });

    if (!r.headersOut['Content-Type']) { r.headersOut['Content-Type'] = JSON_TYPE; }

    r.headersOut['X-Cache'] = cache === 'hit' ? 'HIT' : 'MISS';

    log(r, ctx, 'info', 'request', {
        status: reply.status, source: result.source, cache: cache,
        bytes: reply.body.length,
    });

    r.return(reply.status, reply.body);
}

export default { handle };
