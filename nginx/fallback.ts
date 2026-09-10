/// <reference path="types/ngx_http_js_module.d.ts" />

// fallback.ts — nginx njs proxy with Redis cache + BigQuery fallback

import crypto from 'crypto';

/** Webdis cache endpoint on the sidecar container. */
const WEBDIS = 'http://127.0.0.1:7379';

/** Headers forwarded to PostgREST, in this order; absent or empty ones are skipped. */
const FORWARDED_HEADERS = ['Authorization', 'Accept-Profile', 'Prefer', 'Range', 'Accept'];

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
}

/**
 * The request values that the handler reads from the nginx request and passes
 * to the query helpers.
 */
interface RequestContext extends CacheKeyParts {
    upstream: string;
    cacheTtl: string;
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
        parts.headers['Authorization'] || '', parts.headers['Range'] || '',
        parts.headers['Prefer'] || '', parts.headers['Accept'] || '',
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

    return {
        method: r.method,
        uri: r.uri,
        args: r.variables.args || '',
        sub: jwt.sub,
        schemas: jwt.schemas,
        profile: r.headersIn['Accept-Profile'] || '',
        headers: buildHeaders(r),
        upstream: r.variables.fallback_pgrst || '',
        cacheTtl: r.variables.fallback_cache_ttl || '',
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
 * Queries the local PostgREST upstream.
 *
 * @param r - Current request, used to report a query failure.
 * @param ctx - Request values, with the upstream address, path, query string,
 *   method and forwarded headers.
 * @returns The upstream status and body, or null when the call failed.
 */
async function queryUpstream(r: NginxHTTPRequest, ctx: RequestContext): Promise<ProxyResponse | null> {
    try {
        var res = await ngx.fetch(upstreamUrl(ctx, ctx.uri), {
            method: ctx.method,
            headers: ctx.headers,
        });

        var body = await res.text();

        if (res.status >= 500) {
            log(r, ctx, 'warn', 'upstream-status', { status: res.status });
        }

        return { status: res.status, body: body };
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

        return { status: 200, body: body };
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
 * that a rejected store is reported instead of counted as stored.
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
        var res = await ngx.fetch(WEBDIS + '/SETEX/' + key + '/' + ctx.cacheTtl + '/' + encoded);
        var text = await res.text();
        var answer = JSON.parse(text).SETEX;

        if (answer && answer[0] === true) { return true; }

        log(r, ctx, 'warn', 'cache-write-rejected', { key: key, answer: text.substring(0, 200) });
    } catch (e) {
        log(r, ctx, 'warn', 'cache-write-failed', { key: key, error: String(e) });
    }

    return false;
}

/**
 * Serves one request: cache lookup, local PostgREST query, BigQuery fallback
 * and cache store.
 *
 * @param r - Current request. The upstream address and the cache lifetime come
 *   from the fallback_pgrst and fallback_cache_ttl nginx variables.
 * @returns A promise that settles once the response has been sent.
 */
async function handle(r: NginxHTTPRequest): Promise<void> {
    var ctx = requestContext(r);
    var key = hashKey(ctx);

    var cached = await readCache(r, ctx, key);

    if (cached !== null) {
        r.headersOut['Content-Type'] = 'application/json; charset=utf-8';
        r.headersOut['X-Cache'] = 'HIT';
        log(r, ctx, 'info', 'request', {
            status: 200, source: 'cache', cache: 'hit', bytes: cached.length,
        });
        r.return(200, cached);
        return;
    }

    var response = await queryUpstream(r, ctx);

    if (response === null) {
        var unavailable = '{"error":"PostgREST unavailable"}';

        log(r, ctx, 'info', 'request', {
            status: 502, source: 'none', cache: 'none', bytes: unavailable.length,
        });
        r.return(502, unavailable);
        return;
    }

    var source: AnswerSource = 'postgrest';

    if (ctx.method === 'GET' && response.status === 200 && isEmpty(response.body)) {
        var fallback = await queryFallback(r, ctx);

        if (fallback !== null) {
            response = fallback;
            source = 'bigquery';
        }
    }

    var cache: CacheOutcome = 'none';

    if (response.status === 200 && !isEmpty(response.body)) {
        var stored = await writeCache(r, ctx, key, response.body);

        if (stored) { cache = 'stored'; }
    }

    r.headersOut['Content-Type'] = 'application/json; charset=utf-8';
    r.headersOut['X-Cache'] = 'MISS';

    log(r, ctx, 'info', 'request', {
        status: response.status, source: source, cache: cache, bytes: response.body.length,
    });

    r.return(response.status, response.body);
}

export default { handle };
