/// <reference path="../node_modules/njs-types/ngx_http_js_module.d.ts" />

import crypto from 'crypto';

const WEBDIS = 'http://127.0.0.1:7379';

const FORWARDED_HEADERS = [
    'Authorization', 'Accept-Profile', 'Content-Profile', 'Prefer', 'Range', 'Accept', 'Content-Type',
];

const FORWARDED_RESPONSE_HEADERS = [
    'Content-Type', 'Content-Range', 'Location', 'Preference-Applied', 'WWW-Authenticate',
];

const DEFAULT_MEDIA_TYPE = 'application/json';
const JSON_TYPE = 'application/json; charset=utf-8';
const NO_FALLBACK_PATHS = ['/freshness', '/access_policy'];

interface SyncTable {
    name: string;
    fallback?: boolean;
    cache_ttl?: number;
}

interface SyncSchema {
    tables?: SyncTable[];
}

interface SyncConfig {
    schemas?: Record<string, SyncSchema>;
}

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

type AnswerSource = 'cache' | 'postgrest' | 'bigquery' | 'none';
type LogLevel = 'info' | 'warn';

declare const sync: SyncConfig | undefined;

const inFlight: Record<string, Promise<SharedAnswer>> = {};

/**
 * Reports whether a path is one that the views never cover.
 */
function skipsFallback(uri: string): boolean {
    let skip = false;
    NO_FALLBACK_PATHS.forEach((p) => {
        if (uri === p || uri.startsWith(p + '/')) {
            skip = true;
        }
    });
    return skip;
}

/**
 * Reports whether one schema holds a table with this name, and returns it.
 */
function findTable(schema: SyncSchema, name: string): SyncTable | null {
    if (!schema.tables) { return null; }

    const tables = schema.tables;
    let found: SyncTable | null = null;

    Object.keys(tables).forEach((index) => {
        if (found) { return; }

        const table = tables[Number(index)];
        const parts = table.name.split('.');

        if (parts[parts.length - 1] === name) {
            found = table;
        }
    });

    return found;
}

/**
 * Returns the configured table of a request, or null when it has none.
 */
function tableFor(uri: string, profile: string): SyncTable | null {
    if (skipsFallback(uri)) { return null; }
    if (typeof sync === 'undefined' || !sync.schemas) { return null; }

    const schemas = sync.schemas;
    const name = uri.split('?')[0].split('/')[1];

    if (profile) {
        return profile in schemas ? findTable(schemas[profile], name) : null;
    }

    let found: SyncTable | null = null;

    Object.keys(schemas).forEach((key) => {
        if (found) { return; }
        found = findTable(schemas[key], name);
    });

    return found;
}

/**
 * Reduces an Accept header to the media type that decides the answer format.
 */
function normalizeAccept(value: string): string {
    if (!value) { return DEFAULT_MEDIA_TYPE; }

    const media = value.split(',')[0].split(';')[0].trim().toLowerCase();

    if (media === '' || media === '*/*') { return DEFAULT_MEDIA_TYPE; }

    return media;
}

/**
 * Reads the identity and the schemas claim from a bearer token.
 *
 * The signature is not verified here. The same Authorization header is sent to
 * PostgREST, which validates the token, so this function only reads the claims
 * that take part in the cache key.
 */
function decodeJWT(header: string): { sub: string, schemas: string } {
    if (!header || !header.startsWith('Bearer ')) {
        return { sub: 'anon', schemas: '' };
    }

    try {
        const parts = header.substring(7).split('.');

        if (parts.length < 2) {
            return { sub: 'anon', schemas: '' };
        }

        const claims = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
        let sub = claims.preferred_username || claims.sub || 'anon';
        let schemas = claims.schemas || '';

        if (Array.isArray(schemas)) { schemas = schemas.join(','); }

        return { sub: sub, schemas: schemas };
    } catch (e) {
        return { sub: 'anon', schemas: '' };
    }
}

/**
 * Reports whether a PostgREST response carries no rows.
 */
function isEmpty(body: string): boolean {
    if (!body || body.trim() === '') { return true; }
    const t = body.trim();
    return t === '[]' || t === 'null';
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
    return encodeURIComponent(path).replace(/%2F/g, '/');
}

/**
 * Builds the URL of an upstream request.
 */
function upstreamUrl(ctx: RequestContext, path: string): string {
    return ctx.upstream + encodePath(path) + (ctx.args ? '?' + ctx.args : '');
}

/**
 * Reads one header from an upstream answer.
 *
 * The engine exposes the headers of a fetch answer in more than one shape, so
 * both a headers object with a getter and a plain object are accepted.
 */
function readHeader(raw: unknown, name: string): string {
    const source = raw as { get?: (key: string) => string | null } & Record<string, string>;

    if (typeof source.get === 'function') { return source.get(name) || ''; }

    return source[name] || source[name.toLowerCase()] || '';
}

/**
 * Copies the request headers that PostgREST needs onto the outgoing request.
 */
function buildHeaders(r: NginxHTTPRequest): Record<string, string> {
    const h: Record<string, string> = {};

    FORWARDED_HEADERS.forEach((name) => {
        const value = r.headersIn[name];
        if (value) { h[name] = value; }
    });

    return h;
}

/**
 * Copies the headers that a client must see from an upstream answer.
 */
function responseHeaders(res: unknown): Record<string, string> {
    const raw = (res as { headers?: unknown }).headers;
    const out: Record<string, string> = {};

    if (!raw) { return out; }

    FORWARDED_RESPONSE_HEADERS.forEach((name) => {
        const value = readHeader(raw, name);
        if (value) { out[name] = value; }
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
    const type = (response.headers['Content-Type'] || '').toLowerCase();
    return type === '' || type.indexOf('json') !== -1;
}

/**
 * Builds the cache key of a request.
 *
 * The identity claims and the representation headers are part of the key, so
 * that two users, or two content negotiations, never share a cache entry.
 */
function hashKey(parts: CacheKeyParts): string {
    const input = JSON.stringify([
        parts.method, parts.uri, parts.args, parts.sub, parts.schemas, parts.profile,
        parts.headers['Range'] || '', parts.headers['Prefer'] || '',
        normalizeAccept(parts.headers['Accept'] || ''),
    ]);

    return crypto.createHash('sha256').update(input).digest('hex');
}

/**
 * Reads the values that the handler and the query helpers work with.
 */
function requestContext(r: NginxHTTPRequest): RequestContext {
    const jwt = decodeJWT(r.headersIn['Authorization'] || '');
    const profile = r.headersIn['Accept-Profile'] || '';
    const table = tableFor(r.uri, profile);
    const lifetime = r.variables.fallback_cache_ttl || '';

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
 */
function log(
    r: NginxHTTPRequest, ctx: RequestContext, level: LogLevel, event: string, fields: LogFields
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

    if (level === 'warn') {
        r.warn(line);
        return;
    }

    r.log(line);
}

/**
 * Returns the cached body for a key.
 */
async function readCache(
    r: NginxHTTPRequest, ctx: RequestContext, key: string
): Promise<string | null> {
    try {
        const res = await ngx.fetch(WEBDIS + '/GET/' + key);

        if (res.status !== 200) { return null; }

        const text = await res.text();
        const data = JSON.parse(text);

        if (data.GET && typeof data.GET === 'string') { return data.GET; }
    } catch (e) {
        log(r, ctx, 'warn', 'cache-read-failed', { key: key, error: String(e) });
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
 */
async function writeCache(
    r: NginxHTTPRequest, ctx: RequestContext, key: string, body: string
): Promise<boolean> {
    try {
        const encoded = encodeURIComponent(body);
        const res = await ngx.fetch(WEBDIS + '/', {
            method: 'POST',
            body: 'SETEX/' + key + '/' + ctx.cacheTtl + '/' + encoded,
        });
        const text = await res.text();
        const answer = JSON.parse(text).SETEX;

        if (answer && answer[0] === true) { return true; }

        log(r, ctx, 'warn', 'cache-write-rejected', { key: key, answer: text.substring(0, 200) });
    } catch (e) {
        log(r, ctx, 'warn', 'cache-write-failed', { key: key, error: String(e) });
    }

    return false;
}

/**
 * Queries the local PostgREST upstream.
 */
async function queryUpstream(r: NginxHTTPRequest, ctx: RequestContext): Promise<ProxyResponse | null> {
    try {
        const res = await ngx.fetch(upstreamUrl(ctx, ctx.uri), {
            method: ctx.method,
            headers: ctx.headers,
            body: ctx.body || undefined,
        });

        const body = await res.text();

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
 */
async function queryFallback(r: NginxHTTPRequest, ctx: RequestContext): Promise<ProxyResponse | null> {
    try {
        const res = await ngx.fetch(upstreamUrl(ctx, ctx.uri + '_bq'), {
            method: 'GET',
            headers: ctx.headers,
        });

        if (res.status !== 200) {
            log(r, ctx, 'warn', 'fallback-status', { status: res.status });
            return null;
        }

        const body = await res.text();

        if (isEmpty(body)) { return null; }

        return { status: 200, body: body, headers: responseHeaders(res) };
    } catch (e) {
        log(r, ctx, 'warn', 'fallback-failed', { error: String(e) });
    }

    return null;
}

/**
 * Reads the cache, queries the upstream and escalates to the BigQuery view when
 * the answer is empty.
 */
async function fetchAnswer(
    r: NginxHTTPRequest, ctx: RequestContext, key: string
): Promise<SharedAnswer> {
    if (ctx.method === 'GET') {
        const cached = await readCache(r, ctx, key);

        if (cached !== null) {
            return { response: { status: 200, body: cached, headers: {} }, source: 'cache', leading: true };
        }
    }

    const response = await queryUpstream(r, ctx);

    if (response === null) { return { response: null, source: 'none', leading: true }; }

    if (ctx.method === 'GET' && ctx.fallback && response.status === 200 && isEmpty(response.body)) {
        const fallback = await queryFallback(r, ctx);

        if (fallback !== null) { return { response: fallback, source: 'bigquery', leading: true }; }
    }

    return { response: response, source: 'postgrest', leading: true };
}

/**
 * Answers one request, sharing the call with the requests that ask for the same
 * key at the same time, so that a burst reaches the upstream once.
 */
async function answer(r: NginxHTTPRequest, ctx: RequestContext, key: string): Promise<SharedAnswer> {
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
 * Serves one request: cache lookup, local PostgREST query, BigQuery fallback
 * and cache store.
 */
async function handle(r: NginxHTTPRequest): Promise<void> {
    try {
        const ctx = requestContext(r);
        const key = hashKey(ctx);
        const result = await answer(r, ctx, key);

        if (result.response === null) {
            const unavailable = '{"error":"PostgREST unavailable"}';

            log(r, ctx, 'info', 'request', {
                status: 502, source: 'none', bytes: unavailable.length,
            });

            r.headersOut['X-Source'] = 'none';
            r.return(502, unavailable);
            return;
        }

        const reply = result.response;

        if (result.leading && result.source !== 'cache' && ctx.method === 'GET' && reply.status === 200
            && !ctx.headers['Range'] && !isEmpty(reply.body) && cacheable(reply)) {
            if (ctx.maxBody > 0 && reply.body.length > ctx.maxBody) {
                log(r, ctx, 'warn', 'cache-body-too-large', { bytes: reply.body.length });
            } else {
                await writeCache(r, ctx, key, reply.body);
            }
        }

        Object.keys(reply.headers).forEach((name) => {
            r.headersOut[name] = reply.headers[name];
        });

        if (!r.headersOut['Content-Type']) { r.headersOut['Content-Type'] = JSON_TYPE; }

        r.headersOut['X-Source'] = result.source;
        r.headersOut['X-Cache'] = result.source === 'cache' ? 'HIT' : 'MISS';

        log(r, ctx, 'info', 'request', {
            status: reply.status, source: result.source,
            bytes: reply.body.length,
        });

        r.return(reply.status, reply.body);
    } catch (e) {
        const error = e as { stack?: string };
        r.warn(JSON.stringify({
            event: 'exception',
            error: String(e),
            stack: error.stack,
        }));
        r.return(502, '{"error":"proxy exception"}');
    }
}

export default { handle };
