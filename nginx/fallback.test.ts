import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import proxy from './fallback.ts';

/** An answer that the fake upstream returns for the first matching URL. */
interface FakeAnswer {
    match: string;
    status?: number;
    body?: string;
    throws?: string;
    headers?: Record<string, string>;
}

/** One case of the proxy: the fake answers and the outcome they must produce. */
interface Scenario {
    name: string;
    answers: FakeAnswer[];
    method?: string;
    uri?: string;
    args?: string;
    token?: string;
    profile?: string;
    upstream?: string;
    cacheTtl?: string;
    sync?: unknown;
    requestBody?: string;
    requestContentType?: string;
    requestContentProfile?: string;
    range?: string;
    status: number;
    body: string;
    contentType: string | null;
    xCache: string | null;
    cache: string;
    source: string;
    events: string[];
    calls: (string | RegExp)[];
    sentBody?: string;
    sentBodyPattern?: RegExp;
    sentContentType?: string;
    sentContentProfile?: string;
    headerShape?: string;
}

/** What one run of the proxy produced. */
interface Outcome {
    status: number | null;
    body: string | null;
    contentType: string | null;
    xCache: string | null;
    calls: string[];
    logs: string[];
    warnings: string[];
    headers: Record<string, string>;
    sentBody: string | null;
    sentContentType: string | null;
    sentContentProfile: string | null;
}

const UPSTREAM = 'http://pgrst:3000';
const PATH = '/protocolo_estado_diario';
const QUERY = 'id_unidade=eq.cras_1';

const CACHE_READ = /^GET http:\/\/127\.0\.0\.1:7379\/GET\/[0-9a-f]{64}$/;
const CACHE_WRITE = /^POST http:\/\/127\.0\.0\.1:7379\/SETEX\/[0-9a-f]{64}\/300\//;
const CACHE_WRITE_WITHOUT_TTL = /^POST http:\/\/127\.0\.0\.1:7379\/SETEX\/[0-9a-f]{64}\/\//;
const CACHE_WRITE_WITH_TABLE_TTL = /^POST http:\/\/127\.0\.0\.1:7379\/SETEX\/[0-9a-f]{64}\/42\//;

const CACHE_ROOT = 'http://127.0.0.1:7379/';

/** The sync configuration the harness preloads; it lists the paths these tests use. */
const DEFAULT_SYNC = {
    schemas: {
        pic: {
            tables: [
                { name: 'proj.dev.protocolo_estado_diario' },
                { name: 'proj.dev.endpoint_participantes' },
                { name: 'proj.dev.t' },
            ],
        },
        other: {
            tables: [
                { name: 'proj.dev.outro' },
            ],
        },
    },
};

const MISS = JSON.stringify({ GET: null });
const HIT = JSON.stringify({ GET: '{"cached":true}' });
const STORED = '{"SETEX":[true,"OK"]}';
const REJECTED = '{"SETEX":[false,"ERR value is not an integer or out of range"]}';

const ROWS = '[{"local":1}]';
const BQ_ROWS = '[{"from":"bq"}]';
const EMPTY = '[]';
const CREATED = '[{"subject":"user-1"}]';
const BIG_ROWS = '[{"value":"' + 'a'.repeat(1_100_000) + '"}]';

const CALL = UPSTREAM + PATH + '?' + QUERY;
const BQ_CALL = UPSTREAM + PATH + '_bq?' + QUERY;

/** Builds a bearer token whose payload holds the given claims. */
function tokenFor(claims: Record<string, unknown>): string {
    return 'Bearer header.' + Buffer.from(JSON.stringify(claims)).toString('base64url');
}

/** Builds a second token that carries the same claims as tokenFor. */
function otherTokenFor(claims: Record<string, unknown>): string {
    return 'Bearer other.' + Buffer.from(JSON.stringify(claims)).toString('base64url');
}

const SCENARIOS: Scenario[] = [
    {
        name: 'serves a cache hit without asking the upstream',
        answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200,
        body: '{"cached":true}',
        contentType: 'application/json; charset=utf-8',
        xCache: 'HIT',
        cache: 'hit',
        source: 'cache',
        events: [],
        calls: [CACHE_READ],
    },
    {
        name: 'asks the upstream on a cache miss and stores the answer',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'serves rows from the fallback when the upstream answer is empty',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: '_bq', status: 200, body: BQ_ROWS },
            { match: UPSTREAM, status: 200, body: EMPTY },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        status: 200,
        body: BQ_ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'bigquery',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, 'GET ' + BQ_CALL, CACHE_WRITE],
    },
    {
        name: 'keeps the empty upstream answer when the fallback is empty too',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: '_bq', status: 200, body: EMPTY },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        status: 200,
        body: EMPTY,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, 'GET ' + BQ_CALL],
    },
    {
        name: 'answers 502 without headers when the upstream cannot be reached',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, throws: 'connection refused' },
        ],
        status: 502,
        body: '{"error":"PostgREST unavailable"}',
        contentType: null,
        xCache: null,
        cache: 'none',
        source: 'none',
        events: ['upstream-failed'],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'never runs the fallback for a write request',
        answers: [
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        method: 'POST',
        status: 200,
        body: EMPTY,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: ['POST ' + CALL],
    },
    {
        name: 'never asks BigQuery for an endpoint without a view',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        uri: '/freshness',
        status: 200,
        body: EMPTY,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + UPSTREAM + '/freshness?' + QUERY],
    },
    {
        name: 'never asks BigQuery for a table whose config disables the fallback',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        sync: { schemas: { pic: { tables: [{ name: 'proj.dev.protocolo_estado_diario', fallback: false }] } } },
        status: 200,
        body: EMPTY,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'never asks BigQuery for a schema that is not configured',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        sync: { schemas: { other: { tables: [{ name: 'proj.dev.protocolo_estado_diario' }] } } },
        status: 200,
        body: EMPTY,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'never asks BigQuery for a schema without tables',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        sync: { schemas: { pic: {} } },
        status: 200,
        body: EMPTY,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'never asks BigQuery without a preloaded config',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        sync: null,
        status: 200,
        body: EMPTY,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'stores a large answer through the webdis request body',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: BIG_ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        status: 200,
        body: BIG_ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
        sentBodyPattern: /^SETEX\/[0-9a-f]{64}\/300\//,
    },
    {
        name: 'passes a range answer through and keeps it out of the cache',
        range: '0-0',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS, headers: { 'Content-Range': '0-0/*' } },
        ],
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'passes a csv answer through and keeps it out of the cache',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS, headers: { 'Content-Type': 'text/csv', 'Preference-Applied': 'count=exact' } },
        ],
        status: 200,
        body: ROWS,
        contentType: 'text/csv',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'takes the upstream headers from a plain object too',
        range: '0-1',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS, headers: { 'content-range': '0-1/2' } },
        ],
        headerShape: 'plain',
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'asks the upstream without a query string',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        args: '',
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + UPSTREAM + PATH, CACHE_WRITE],
    },
    {
        name: 'never asks BigQuery for a table that no schema configures',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        profile: '',
        uri: '/desconhecido',
        status: 200,
        body: EMPTY,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + UPSTREAM + '/desconhecido?' + QUERY],
    },
    {
        name: 'takes the lifetime from the table when it carries one',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        sync: { schemas: { pic: { tables: [{ name: 'proj.dev.protocolo_estado_diario', cache_ttl: 42 }] } } },
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE_WITH_TABLE_TTL],
        sentBodyPattern: /^SETEX\/[0-9a-f]{64}\/42\//,
    },
    {
        name: 'keeps an answer above the stored size out of the cache',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: BIG_ROWS },
        ],
        maxBody: 10,
        status: 200,
        body: BIG_ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: ['cache-body-too-large'],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'forwards the body and the content headers of a write',
        answers: [{ match: UPSTREAM, status: 201, body: CREATED }],
        method: 'POST',
        requestBody: CREATED,
        requestContentType: 'application/json',
        requestContentProfile: 'pic',
        status: 201,
        body: CREATED,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: ['POST ' + CALL],
        sentBody: CREATED,
        sentContentType: 'application/json',
        sentContentProfile: 'pic',
    },
    {
        name: 'keeps the upstream answer when the fallback call fails',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: '_bq', throws: 'bq down' },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        status: 200,
        body: EMPTY,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: ['fallback-failed'],
        calls: [CACHE_READ, 'GET ' + CALL, 'GET ' + BQ_CALL],
    },
    {
        name: 'keeps the upstream answer when the fallback answers an error status',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: '_bq', status: 500, body: '{"message":"duckdb failed"}' },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        status: 200,
        body: EMPTY,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: ['fallback-status'],
        calls: [CACHE_READ, 'GET ' + CALL, 'GET ' + BQ_CALL],
    },
    {
        name: 'passes an upstream server error through and reports it',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 503, body: '{"message":"down"}' },
        ],
        status: 503,
        body: '{"message":"down"}',
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: ['upstream-status'],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'reports a cache write that could not be sent',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', throws: 'webdis down' },
        ],
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: ['cache-write-failed'],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'reports a cache write that the cache rejected',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: REJECTED },
        ],
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: ['cache-write-rejected'],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'keeps a decoded question mark inside the path',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        uri: '/endpoint_participantes?x',
        args: '',
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + UPSTREAM + '/endpoint_participantes%3Fx', CACHE_WRITE],
    },
    {
        name: 'keeps the fallback suffix inside a path that carries a decoded question mark',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: '_bq', status: 200, body: BQ_ROWS },
            { match: UPSTREAM, status: 200, body: EMPTY },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        uri: '/t?x',
        args: '',
        status: 200,
        body: BQ_ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'bigquery',
        events: [],
        calls: [CACHE_READ, 'GET ' + UPSTREAM + '/t%3Fx', 'GET ' + UPSTREAM + '/t%3Fx_bq', CACHE_WRITE],
    },
    {
        name: 'reads the claims of a token',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        token: tokenFor({ sub: 'alice', schemas: 'pic' }),
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'joins a schemas claim that is an array',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        token: tokenFor({ preferred_username: 'bob', schemas: ['pic', 'other'] }),
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'treats a request without an authorization header as anonymous',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        token: '',
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'treats a token without a payload as anonymous',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        token: 'Bearer not-a-token',
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'reports a cache read that answers something unusable',
        answers: [
            { match: '/GET/', status: 200, body: 'not json' },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: ['cache-read-failed'],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'treats a failed cache read as a miss',
        answers: [
            { match: '/GET/', status: 500, body: '{"GET":null}' },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'treats a token without claims as anonymous',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        token: tokenFor({}),
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'treats a header that is not a bearer token as anonymous',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        token: 'Basic abc',
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'treats a token whose payload is not json as anonymous',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        token: 'Bearer header.!!!',
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'treats a cache entry that is not text as a miss',
        answers: [
            { match: '/GET/', status: 200, body: '{"GET":123}' },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'reports a cache write that the cache did not acknowledge',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: '{}' },
        ],
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: ['cache-write-rejected'],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'ignores a fallback answer that is an empty body',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: '_bq', status: 200, body: '' },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        status: 200,
        body: EMPTY,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, 'GET ' + BQ_CALL],
    },
    {
        name: 'runs without a profile and with an empty upstream and lifetime',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: PATH, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        profile: '',
        upstream: '',
        cacheTtl: '',
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + PATH + '?' + QUERY, CACHE_WRITE_WITHOUT_TTL],
        sentBodyPattern: /^SETEX\/[0-9a-f]{64}\/\//,
    },
];

const BASE_FIELDS = ['event', 'method', 'uri', 'wait'];

/** Builds the fake upstream that the module under test calls. */
function fakeUpstream(scenario: Scenario): {
    fetch: (url: string, options?: { method?: string, headers?: Record<string, string>, body?: string }) => Promise<{ status: number, text: () => Promise<string> }>,
    calls: string[],
    sent: { body: string | null, contentType: string | null, contentProfile: string | null },
} {
    const calls: string[] = [];
    const sent: { body: string | null, contentType: string | null, contentProfile: string | null } = {
        body: null, contentType: null, contentProfile: null,
    };

    const fetch = async (
        url: string, options?: { method?: string, headers?: Record<string, string>, body?: string }
    ): Promise<{ status: number, text: () => Promise<string> }> => {
        const method = options && options.method ? options.method : 'GET';
        const sentBody = options && options.body !== undefined ? options.body : null;
        const command = url === CACHE_ROOT && typeof sentBody === 'string' ? sentBody : '';
        const call = method + ' ' + url + command;

        calls.push(call);
        sent.body = sentBody;
        const outgoing = (options && options.headers) || {};
        sent.contentType = outgoing['Content-Type'] || null;
        sent.contentProfile = outgoing['Content-Profile'] || null;

        const answer = scenario.answers.find((entry) => call.indexOf(entry.match) !== -1);

        if (!answer) {
            throw new Error('unexpected upstream call: ' + url);
        }

        if (answer.throws) { throw new Error(answer.throws); }

        const body = answer.body || '';
        const headers = answer.headers || {};
        const shaped = scenario.headerShape === 'plain'
            ? headers
            : (Object.keys(headers).length > 0 ? { get: (name: string) => headers[name] || headers[name.toLowerCase()] || null } : null);

        return { status: answer.status || 0, text: async () => body, headers: shaped };
    };

    return { fetch: fetch, calls: calls, sent: sent };
}

/** Builds the request object that the module under test reads. */
function fakeRequest(
    scenario: Scenario,
    headersOut: Record<string, string>,
    response: { status: number | null, body: string | null },
    logs: string[],
    warnings: string[],
): unknown {
    const headersIn: Record<string, string> = {};
    const token = scenario.token === undefined ? 'Bearer secret-token' : scenario.token;
    const profile = scenario.profile === undefined ? 'pic' : scenario.profile;

    if (token) { headersIn['Authorization'] = token; }
    if (profile) { headersIn['Accept-Profile'] = profile; }
    if (scenario.accept) { headersIn['Accept'] = scenario.accept; }
    if (scenario.requestContentType) { headersIn['Content-Type'] = scenario.requestContentType; }
    if (scenario.requestContentProfile) { headersIn['Content-Profile'] = scenario.requestContentProfile; }
    if (scenario.range) { headersIn['Range'] = scenario.range; }

    return {
        uri: scenario.uri || PATH,
        method: scenario.method || 'GET',
        headersIn: headersIn,
        requestText: scenario.requestBody || '',
        headersOut: headersOut,
        variables: {
            args: scenario.args === undefined ? QUERY : scenario.args,
            fallback_pgrst: scenario.upstream === undefined ? UPSTREAM : scenario.upstream,
            fallback_cache_ttl: scenario.cacheTtl === undefined ? '300' : scenario.cacheTtl,
            fallback_max_body: scenario.maxBody === undefined ? '' : String(scenario.maxBody),
        },
        log: (message: string) => { logs.push(message); },
        warn: (message: string) => { warnings.push(message); },
        return: (status: number, body: string) => { response.status = status; response.body = body; },
    };
}

/** Publishes the globals that the module under test reads. */
function setGlobals(scenario: Scenario, fetch: unknown): void {
    const globals = globalThis as unknown as { sync?: unknown, ngx?: unknown };

    if (scenario.sync === null) {
        delete globals.sync;
    } else {
        globals.sync = scenario.sync === undefined ? DEFAULT_SYNC : scenario.sync;
    }

    globals.ngx = { fetch: fetch };
}

/** Runs one scenario against the module under test. */
async function run(scenario: Scenario): Promise<Outcome> {
    const logs: string[] = [];
    const warnings: string[] = [];
    const headersOut: Record<string, string> = {};
    const response: { status: number | null, body: string | null } = { status: null, body: null };
    const upstream = fakeUpstream(scenario);
    const request = fakeRequest(scenario, headersOut, response, logs, warnings);

    setGlobals(scenario, upstream.fetch);

    await proxy.handle(request as unknown as NginxHTTPRequest);

    return {
        status: response.status,
        body: response.body,
        contentType: headersOut['Content-Type'] || null,
        xCache: headersOut['X-Cache'] || null,
        calls: upstream.calls,
        logs: logs,
        warnings: warnings,
        headers: headersOut,
        sentBody: upstream.sent.body,
        sentContentType: upstream.sent.contentType,
        sentContentProfile: upstream.sent.contentProfile,
    };
}

function assertCalls(actual: string[], expected: (string | RegExp)[]): void {
    assert.equal(actual.length, expected.length, 'the number of upstream calls differs');

    expected.forEach((pattern, index) => {
        if (typeof pattern === 'string') {
            assert.equal(actual[index], pattern);
            return;
        }

        assert.match(actual[index], pattern);
    });
}

/** Parses a log line, so that a case reports a readable failure. */
function parseLine(line: string, kind: string): Record<string, unknown> {
    let parsed: Record<string, unknown>;

    try {
        parsed = JSON.parse(line);
    } catch (error) {
        throw new Error(kind + ' line is not JSON: ' + line);
    }

    return parsed;
}

function assertBaseFields(parsed: Record<string, unknown>, kind: string): void {
    BASE_FIELDS.forEach((field) => {
        if (!(field in parsed)) {
            throw new Error(kind + ' line has no ' + field + ' field: ' + JSON.stringify(parsed));
        }
    });
}

SCENARIOS.forEach((scenario) => {
    test('proxy: ' + scenario.name, async () => {
        const outcome = await run(scenario);

        assert.equal(outcome.status, scenario.status, 'status differs');
        assert.equal(outcome.body, scenario.body, 'body differs');
        assert.equal(outcome.contentType, scenario.contentType, 'content type differs');
        assert.equal(outcome.xCache, scenario.xCache, 'cache header differs');
        assertCalls(outcome.calls, scenario.calls);
        if (scenario.sentBody !== undefined) {
            assert.equal(outcome.sentBody, scenario.sentBody, 'the body sent to the upstream differs');
        }
        if (scenario.sentBodyPattern) {
            assert.match(
                outcome.sentBody || '',
                scenario.sentBodyPattern,
                'the body sent to the upstream does not match',
            );
        }
        assert.equal(
            outcome.sentContentType,
            scenario.sentContentType === undefined ? null : scenario.sentContentType,
            'the content type sent to the upstream differs',
        );
        assert.equal(
            outcome.sentContentProfile,
            scenario.sentContentProfile === undefined ? null : scenario.sentContentProfile,
            'the content profile sent to the upstream differs',
        );

        assert.equal(outcome.logs.length, 1, 'a request must write exactly one summary line');

        const summary = parseLine(outcome.logs[0], 'summary');
        assertBaseFields(summary, 'summary');
        assert.equal(summary.status, scenario.status, 'the summary status differs');
        assert.equal(summary.cache, scenario.cache, 'the summary cache outcome differs');
        assert.equal(summary.source, scenario.source, 'the summary source differs');
        assert.equal(summary.bytes, scenario.body.length, 'the summary byte size differs');

        assert.equal(outcome.warnings.length, scenario.events.length, 'the number of failures differs');

        scenario.events.forEach((event, index) => {
            const failure = parseLine(outcome.warnings[index], 'failure');

            assertBaseFields(failure, 'failure');
            assert.equal(failure.event, event, 'the failure event differs');
        });

        outcome.logs.concat(outcome.warnings).forEach((line) => {
            assert.equal(line.indexOf('secret-token'), -1, 'a log line carries the token');
            assert.equal(line.indexOf(QUERY), -1, 'a log line carries the query string');
        });
    });
});

test('proxy: keys a media type the same with and without parameters', async () => {
    const answers = [{ match: '/GET/', status: 200, body: HIT }];
    const result = { status: 200, body: '{"cached":true}', contentType: 'application/json; charset=utf-8', xCache: 'HIT', cache: 'hit', source: 'cache', events: [], calls: [CACHE_READ] };

    const bare = await run({ name: 'bare', accept: 'application/json', answers: answers, ...result });
    const parameters = await run({ name: 'parameters', accept: 'application/json; charset=utf-8', answers: answers, ...result });
    const wildcard = await run({ name: 'wildcard', accept: '*/*', answers: answers, ...result });
    const empty = await run({ name: 'empty', accept: ', text/html', answers: answers, ...result });

    assert.equal(bare.calls[0], parameters.calls[0], 'the parameters must not split the key');
    assert.equal(bare.calls[0], wildcard.calls[0], 'a wildcard must use the default media type');
    assert.equal(bare.calls[0], empty.calls[0], 'a list without a media type must use the default');
});

test('proxy: keeps a media type that asks for another format apart', async () => {
    const answers = [{ match: '/GET/', status: 200, body: HIT }];
    const result = { status: 200, body: '{"cached":true}', contentType: 'application/json; charset=utf-8', xCache: 'HIT', cache: 'hit', source: 'cache', events: [], calls: [CACHE_READ] };

    const json = await run({ name: 'json', accept: 'application/json', answers: answers, ...result });
    const csv = await run({ name: 'csv', accept: 'text/csv', answers: answers, ...result });

    assert.notEqual(json.calls[0], csv.calls[0], 'the answer format must be part of the key');
});

test('proxy: shares an entry between tokens with the same claims', async () => {
    const answers = [{ match: '/GET/', status: 200, body: HIT }];
    const result = { status: 200, body: '{"cached":true}', contentType: 'application/json; charset=utf-8', xCache: 'HIT', cache: 'hit', source: 'cache', events: [], calls: [CACHE_READ] };

    const first = await run({ name: 'first', token: tokenFor({ sub: 'alice' }), answers: answers, ...result });
    const refreshed = await run({ name: 'refreshed', token: otherTokenFor({ sub: 'alice' }), answers: answers, ...result });

    assert.equal(first.calls[0], refreshed.calls[0], 'a refreshed token must reuse the entry');
});

test('proxy: shares one call between concurrent requests for the same key', async () => {
    const scenario: Scenario = {
        name: 'concurrent',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'stored',
        source: 'postgrest',
        events: [],
        calls: [],
    };
    const upstream = fakeUpstream(scenario);
    const first = fakeRequest(scenario, {}, { status: null, body: null }, [], []);
    const second = fakeRequest(scenario, {}, { status: null, body: null }, [], []);

    setGlobals(scenario, upstream.fetch);

    await Promise.all([
        proxy.handle(first as unknown as NginxHTTPRequest),
        proxy.handle(second as unknown as NginxHTTPRequest),
    ]);

    const backend = upstream.calls.filter((call) => call.indexOf(CACHE_ROOT) === -1);

    assert.equal(backend.length, 1, 'two concurrent requests must reach the upstream once');
});

test('proxy: avoids the globals and methods that the engine does not provide', async () => {
    const source = readFileSync(new URL('./fallback.ts', import.meta.url), 'utf8');
    const unsupported = [
        'Map', 'Set', 'WeakMap', 'WeakSet', 'Proxy', 'Reflect', 'Symbol',
        'filter', 'find', 'findIndex', 'flat', 'flatMap', 'reduce', 'reduceRight', 'includes',
    ];

    unsupported.forEach((name) => {
        const pattern = new RegExp('\\b' + name + '\\b');

        assert.doesNotMatch(source, pattern, 'the njs engine does not provide ' + name);
    });
});

test('proxy: carries the range and preference headers to the client', async () => {
    const outcome = await run({
        name: 'ranged answer',
        range: '0-0',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS, headers: { 'Content-Range': '0-0/*', 'Location': '/protocolo_estado_diario?id=eq.1', 'Preference-Applied': 'count=exact' } },
        ],
        status: 200,
        body: ROWS,
        contentType: 'application/json; charset=utf-8',
        xCache: 'MISS',
        cache: 'none',
        source: 'postgrest',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL],
    });

    assert.equal(outcome.headers['Content-Range'], '0-0/*', 'the range header must reach the client');
    assert.equal(outcome.headers['Location'], '/protocolo_estado_diario?id=eq.1', 'the location header must reach the client');
    assert.equal(outcome.headers['Preference-Applied'], 'count=exact', 'the preference header must reach the client');
});

test('proxy: serves a stored answer without the headers of the live answer', async () => {
    const outcome = await run({
        name: 'stored answer',
        answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200, body: '{"cached":true}', contentType: 'application/json; charset=utf-8',
        xCache: 'HIT', cache: 'hit', source: 'cache', events: [], calls: [CACHE_READ],
    });

    assert.equal(outcome.headers['Content-Range'], undefined, 'a cached answer carries no range header');
});

test('proxy: separates the cache by the token claims', async () => {
    const alice = await run({
        name: 'alice', token: tokenFor({ sub: 'alice' }),
        answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200, body: '{"cached":true}', contentType: 'application/json; charset=utf-8',
        xCache: 'HIT', cache: 'hit', source: 'cache', events: [], calls: [CACHE_READ],
    });

    const bob = await run({
        name: 'bob', token: tokenFor({ sub: 'bob' }),
        answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200, body: '{"cached":true}', contentType: 'application/json; charset=utf-8',
        xCache: 'HIT', cache: 'hit', source: 'cache', events: [], calls: [CACHE_READ],
    });

    assert.notEqual(alice.calls[0], bob.calls[0], 'two subjects must not share a cache key');
});

test('proxy: separates the cache by the profile', async () => {
    const withProfile = await run({
        name: 'with profile', answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200, body: '{"cached":true}', contentType: 'application/json; charset=utf-8',
        xCache: 'HIT', cache: 'hit', source: 'cache', events: [], calls: [CACHE_READ],
    });

    const withoutProfile = await run({
        name: 'without profile', profile: '', answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200, body: '{"cached":true}', contentType: 'application/json; charset=utf-8',
        xCache: 'HIT', cache: 'hit', source: 'cache', events: [], calls: [CACHE_READ],
    });

    assert.notEqual(withProfile.calls[0], withoutProfile.calls[0], 'the profile must be part of the key');
});

test('proxy: reports a failure when no fake answer matches the call', async () => {
    const outcome = await run({
        name: 'no matching answer',
        answers: [{ match: '/GET/', status: 200, body: MISS }],
        status: 502, body: '', contentType: null, xCache: null,
        cache: 'none', source: 'none', events: [], calls: [],
    });

    assert.equal(outcome.status, 502, 'an unmatched call must not look like a success');
    assert.equal(outcome.warnings.length, 1, 'an unmatched call must be reported');

    const failure = parseLine(outcome.warnings[0], 'failure');

    assert.equal(failure.event, 'upstream-failed', 'the failure event differs');
});
