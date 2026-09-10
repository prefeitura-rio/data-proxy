import { test } from 'node:test';
import assert from 'node:assert/strict';

import proxy from './fallback.ts';

/** An answer that the fake upstream returns for the first matching URL. */
interface FakeAnswer {
    match: string;
    status?: number;
    body?: string;
    throws?: string;
}

/** One case of the proxy: the fake answers and the outcome they must produce. */
interface Scenario {
    name: string;
    answers: FakeAnswer[];
    method?: string;
    uri?: string;
    args?: string;
    /** Authorization header; an empty string means no header at all. */
    token?: string;
    /** Accept-Profile header; an empty string means no header at all. */
    profile?: string;
    /** Upstream address; an empty string means the variable is not set. */
    upstream?: string;
    /** Cache lifetime; an empty string means the variable is not set. */
    cacheTtl?: string;
    status: number;
    body: string;
    contentType: string | null;
    xCache: string | null;
    cache: string;
    source: string;
    events: string[];
    calls: (string | RegExp)[];
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
}

const UPSTREAM = 'http://pgrst:3000';
const PATH = '/protocolo_estado_diario';
const QUERY = 'id_unidade=eq.cras_1';

const CACHE_READ = /^GET http:\/\/127\.0\.0\.1:7379\/GET\/[0-9a-f]{64}$/;
const CACHE_WRITE = /^GET http:\/\/127\.0\.0\.1:7379\/SETEX\/[0-9a-f]{64}\/300\//;
const CACHE_WRITE_WITHOUT_TTL = /^GET http:\/\/127\.0\.0\.1:7379\/SETEX\/[0-9a-f]{64}\/\//;

const MISS = JSON.stringify({ GET: null });
const HIT = JSON.stringify({ GET: '{"cached":true}' });
const STORED = '{"SETEX":[true,"OK"]}';
const REJECTED = '{"SETEX":[false,"ERR value is not an integer or out of range"]}';

const ROWS = '[{"local":1}]';
const BQ_ROWS = '[{"from":"bq"}]';
const EMPTY = '[]';

const CALL = UPSTREAM + PATH + '?' + QUERY;
const BQ_CALL = UPSTREAM + PATH + '_bq?' + QUERY;

/** Builds a bearer token whose payload holds the given claims. */
function tokenFor(claims: Record<string, unknown>): string {
    return 'Bearer header.' + Buffer.from(JSON.stringify(claims)).toString('base64url');
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
            { match: '/GET/', status: 200, body: MISS },
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
        calls: [CACHE_READ, 'POST ' + CALL],
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
    },
];

const BASE_FIELDS = ['event', 'method', 'uri', 'wait'];

/** Runs one scenario against the module under test. */
async function run(scenario: Scenario): Promise<Outcome> {
    const calls: string[] = [];
    const logs: string[] = [];
    const warnings: string[] = [];
    const headersOut: Record<string, string> = {};
    const response: { status: number | null, body: string | null } = { status: null, body: null };

    const fetch = async (
        url: string, options?: { method?: string }
    ): Promise<{ status: number, text: () => Promise<string> }> => {
        calls.push((options && options.method ? options.method : 'GET') + ' ' + url);

        const answer = scenario.answers.find((entry) => url.indexOf(entry.match) !== -1);

        if (!answer) {
            throw new Error('unexpected upstream call: ' + url);
        }

        if (answer.throws) { throw new Error(answer.throws); }

        const body = answer.body || '';

        return { status: answer.status || 0, text: async () => body };
    };

    const headersIn: Record<string, string> = {};
    const token = scenario.token === undefined ? 'Bearer secret-token' : scenario.token;
    const profile = scenario.profile === undefined ? 'pic' : scenario.profile;

    if (token) { headersIn['Authorization'] = token; }
    if (profile) { headersIn['Accept-Profile'] = profile; }

    const request = {
        uri: scenario.uri || PATH,
        method: scenario.method || 'GET',
        headersIn: headersIn,
        headersOut: headersOut,
        variables: {
            args: scenario.args === undefined ? QUERY : scenario.args,
            fallback_pgrst: scenario.upstream === undefined ? UPSTREAM : scenario.upstream,
            fallback_cache_ttl: scenario.cacheTtl === undefined ? '300' : scenario.cacheTtl,
        },
        log: (message: string) => { logs.push(message); },
        warn: (message: string) => { warnings.push(message); },
        return: (status: number, body: string) => { response.status = status; response.body = body; },
    };

    (globalThis as unknown as { ngx: unknown }).ngx = { fetch: fetch };

    await proxy.handle(request as unknown as NginxHTTPRequest);

    return {
        status: response.status,
        body: response.body,
        contentType: headersOut['Content-Type'] || null,
        xCache: headersOut['X-Cache'] || null,
        calls: calls,
        logs: logs,
        warnings: warnings,
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
