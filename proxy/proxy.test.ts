import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import proxy from './proxy.ts';

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
    readUpstream?: string;
    cacheTtl?: string;
    sync?: unknown;
    requestBody?: string;
    requestContentType?: string;
    requestContentProfile?: string;
    range?: string;
    accept?: string;
    snapshot?: string;
    maxBody?: number;
    status: number;
    body: string;
    contentType: string | null;
    xCache: string | null;
    source: string;
    xSource?: string;
    xSnapshot?: string;
    events: string[];
    calls: (string | RegExp)[];
    sentBody?: string;
    sentBodyPattern?: RegExp;
    sentContentType?: string;
    sentContentProfile?: string;
    sentSnapshot?: string;
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
    sentSnapshot: string | null;
}

const UPSTREAM = 'http://pgrst:3000';
const PATH = '/partitioned_table';
const QUERY = 'unit_id=eq.unit_1';

const CACHE_READ = /^GET http:\/\/127\.0\.0\.1:7380\/GET\/[0-9a-f]{64}$/;
const CACHE_WRITE = /^POST http:\/\/127\.0\.0\.1:7379\/SETEX\/[0-9a-f]{64}\/300\//;
const CACHE_WRITE_WITH_TABLE_TTL = /^POST http:\/\/127\.0\.0\.1:7379\/SETEX\/[0-9a-f]{64}\/42\//;

const CACHE_READ_ROOT = 'http://127.0.0.1:7380/';
const CACHE_WRITE_ROOT = 'http://127.0.0.1:7379/';

/** The sync configuration the harness preloads; it lists the paths these tests use. */
const DEFAULT_SYNC = {
    schemas: {
        test: {
            tables: [
                { name: 'proj.dev.partitioned_table' },
                { name: 'proj.dev.multi_rls_table' },
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
const STORED_HEADERS = { 'X-Source': 'ducklake', 'X-DuckLake-Snapshot': '7' };
const HIT = JSON.stringify({
    GET: JSON.stringify({ body: '{"cached":true}', headers: STORED_HEADERS }),
});
const STORED = '{"SETEX":[true,"OK"]}';
const REJECTED = '{"SETEX":[false,"ERR value is not an integer or out of range"]}';

const ROWS = '[{"local":1}]';
const EMPTY = '[]';
const CREATED = '[{"subject":"test_user_1"}]';
const BIG_ROWS = '[{"value":"' + 'a'.repeat(1_100_000) + '"}]';

const CALL = UPSTREAM + PATH + '?' + QUERY;
const JSON_CT = 'application/json; charset=utf-8';

/** Builds a bearer token whose payload holds the given claims. */
function tokenFor(claims: Record<string, unknown>, header: string = 'header'): string {
    return 'Bearer ' + header + '.' + Buffer.from(JSON.stringify(claims)).toString('base64url');
}

/** The shared answers for a simple cache-miss-then-store flow. */
const MISS_UPSTREAM_STORE: FakeAnswer[] = [
    { match: '/GET/', status: 200, body: MISS },
    { match: UPSTREAM, status: 200, body: ROWS },
    { match: '/SETEX/', status: 200, body: STORED },
];
const MISS_FLOW_CALLS = [CACHE_READ, 'GET ' + CALL, CACHE_WRITE];

const SCENARIOS: Scenario[] = [
    {
        name: 'serves a cache hit without asking the upstream',
        answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200,
        body: '{"cached":true}',
        contentType: JSON_CT,
        xCache: 'HIT',
        source: 'cache',
        xSource: 'ducklake',
        xSnapshot: '7',
        events: [],
        calls: [CACHE_READ],
    },
    {
        name: 'asks the upstream on a cache miss and stores the answer with its headers',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS, headers: STORED_HEADERS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        status: 200,
        body: ROWS,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'ducklake',
        xSource: 'ducklake',
        xSnapshot: '7',
        events: [],
        calls: MISS_FLOW_CALLS,
        sentBodyPattern: /^SETEX\/[0-9a-f]{64}\/300\/%7B%22body%22/,
    },
    {
        name: 'never stores an empty answer and never asks another source',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        status: 200,
        body: EMPTY,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL],
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
        source: 'none',
        events: ['upstream-failed'],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'does not cache access_policy reads',
        uri: '/access_policy',
        answers: [
            { match: UPSTREAM, status: 200, body: ROWS },
        ],
        status: 200,
        body: ROWS,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: [],
        calls: ['GET ' + UPSTREAM + '/access_policy?' + QUERY],
    },
    {
        name: 'does not cache a write request',
        answers: [
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        method: 'POST',
        status: 200,
        body: EMPTY,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: [],
        calls: ['POST ' + CALL],
    },
    {
        name: 'forwards the body and the content headers of a write',
        answers: [{ match: UPSTREAM, status: 201, body: CREATED }],
        method: 'POST',
        requestBody: CREATED,
        requestContentType: 'application/json',
        requestContentProfile: 'test',
        status: 201,
        body: CREATED,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: [],
        calls: ['POST ' + CALL],
        sentBody: CREATED,
        sentContentType: 'application/json',
        sentContentProfile: 'test',
    },
    {
        name: 'passes an upstream server error through and reports it',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 503, body: '{"message":"down"}' },
        ],
        status: 503,
        body: '{"message":"down"}',
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: ['upstream-status'],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'passes a range answer through with headers and keeps it out of the cache',
        range: '0-0',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS, headers: { 'Content-Range': '0-0/*', 'Location': '/partitioned_table?id=eq.1', 'Preference-Applied': 'count=exact' } },
        ],
        status: 200,
        body: ROWS,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
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
        source: 'upstream',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL],
    },
    {
        name: 'takes the lifetime from the table when it carries one',
        answers: MISS_UPSTREAM_STORE,
        sync: { schemas: { test: { tables: [{ name: 'proj.dev.partitioned_table', cache_ttl: 42 }] } } },
        status: 200,
        body: ROWS,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
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
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: ['cache-body-too-large'],
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
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
        sentBodyPattern: /^SETEX\/[0-9a-f]{64}\/300\//,
    },
    {
        name: 'keeps serving when the cache write fails or is rejected',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: REJECTED },
        ],
        status: 200,
        body: ROWS,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: ['cache-write-rejected'],
        calls: [CACHE_READ, 'GET ' + CALL, CACHE_WRITE],
    },
    {
        name: 'treats a stored answer without the expected shape as a miss',
        answers: [
            { match: '/GET/', status: 200, body: JSON.stringify({ GET: '[{"legacy":true}]' }) },
            { match: UPSTREAM, status: 200, body: ROWS },
            { match: '/SETEX/', status: 200, body: STORED },
        ],
        status: 200,
        body: ROWS,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: [],
        calls: MISS_FLOW_CALLS,
    },
    {
        name: 'encodes a decoded question mark in the path',
        answers: MISS_UPSTREAM_STORE,
        uri: '/t?x',
        args: '',
        status: 200,
        body: ROWS,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: [],
        calls: [CACHE_READ, 'GET ' + UPSTREAM + '/t%3Fx', CACHE_WRITE],
    },
    {
        name: 'asks the upstream without a query string',
        answers: MISS_UPSTREAM_STORE,
        args: '',
        status: 200,
        body: ROWS,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: [],
        calls: [CACHE_READ, 'GET ' + UPSTREAM + PATH, CACHE_WRITE],
    },
    {
        name: 'joins a schemas claim that is an array',
        answers: MISS_UPSTREAM_STORE,
        token: tokenFor({ preferred_username: 'bob', schemas: ['test', 'other'] }),
        status: 200,
        body: ROWS,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: [],
        calls: MISS_FLOW_CALLS,
    },
    {
        name: 'forwards the pinned snapshot version to the upstream',
        snapshot: '5',
        answers: [
            { match: '/GET/', status: 200, body: MISS },
            { match: UPSTREAM, status: 200, body: EMPTY },
        ],
        status: 200,
        body: EMPTY,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: [],
        calls: [CACHE_READ, 'GET ' + CALL],
        sentSnapshot: '5',
    },
];

const BASE_FIELDS = ['event', 'method', 'uri', 'wait_ms'];

/** Builds the fake upstream that the module under test calls. */
function fakeUpstream(scenario: Scenario): {
    fetch: (url: string, options?: { method?: string, headers?: Record<string, string>, body?: string }) => Promise<{ status: number, text: () => Promise<string>, headers: unknown }>,
    calls: string[],
    sent: { body: string | null, contentType: string | null, contentProfile: string | null, snapshot: string | null },
} {
    const calls: string[] = [];
    const sent: { body: string | null, contentType: string | null, contentProfile: string | null, snapshot: string | null } = {
        body: null, contentType: null, contentProfile: null, snapshot: null,
    };

    const fetch = async (
        url: string, options?: { method?: string, headers?: Record<string, string>, body?: string }
    ): Promise<{ status: number, text: () => Promise<string>, headers: unknown }> => {
        const method = options && options.method ? options.method : 'GET';
        const sentBody = options && options.body !== undefined ? options.body : null;
        const command = (url === CACHE_READ_ROOT || url === CACHE_WRITE_ROOT) && typeof sentBody === 'string' ? sentBody : '';
        const call = method + ' ' + url + command;

        calls.push(call);
        sent.body = sentBody;
        const outgoing = (options && options.headers) || {};
        sent.contentType = outgoing['Content-Type'] || null;
        sent.contentProfile = outgoing['Content-Profile'] || null;
        sent.snapshot = outgoing['X-DuckLake-Snapshot'] || null;

        const answer = scenario.answers.find((entry) => call.indexOf(entry.match) !== -1);

        if (!answer && call.indexOf('/SETEX/') !== -1) {
            return { status: 200, text: async () => STORED, headers: null };
        }

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
    const profile = scenario.profile === undefined ? 'test' : scenario.profile;

    if (token) { headersIn['Authorization'] = token; }
    if (profile) { headersIn['Accept-Profile'] = profile; }
    if (scenario.accept) { headersIn['Accept'] = scenario.accept; }
    if (scenario.requestContentType) { headersIn['Content-Type'] = scenario.requestContentType; }
    if (scenario.requestContentProfile) { headersIn['Content-Profile'] = scenario.requestContentProfile; }
    if (scenario.range) { headersIn['Range'] = scenario.range; }
    if (scenario.snapshot) { headersIn['X-DuckLake-Snapshot'] = scenario.snapshot; }

    return {
        uri: scenario.uri || PATH,
        method: scenario.method || 'GET',
        headersIn: headersIn,
        requestText: scenario.requestBody || '',
        headersOut: headersOut,
        variables: {
            args: scenario.args === undefined ? QUERY : scenario.args,
            postgrest_read: scenario.readUpstream === undefined
                ? (scenario.upstream === undefined ? UPSTREAM : scenario.upstream)
                : scenario.readUpstream,
            proxy_cache_ttl: scenario.cacheTtl === undefined ? '300' : scenario.cacheTtl,
            proxy_max_body: scenario.maxBody === undefined ? '' : String(scenario.maxBody),
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
        sentSnapshot: upstream.sent.snapshot,
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
        assert.equal(outcome.headers['X-Source'], scenario.xSource, 'source header differs');
        assert.equal(outcome.headers['X-DuckLake-Snapshot'], scenario.xSnapshot, 'snapshot header differs');
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
        assert.equal(
            outcome.sentSnapshot,
            scenario.sentSnapshot === undefined ? null : scenario.sentSnapshot,
            'the snapshot version sent to the upstream differs',
        );

        assert.equal(outcome.logs.length, 1, 'a request must write exactly one summary line');

        const summary = parseLine(outcome.logs[0], 'summary');
        assertBaseFields(summary, 'summary');
        assert.equal(summary.status, scenario.status, 'the summary status differs');
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

test('proxy: uses the single read upstream for all methods', async () => {
    const read = 'http://postgrest-read:3000';
    const common = {
        readUpstream: read,
        status: 200,
        body: ROWS,
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
        events: [],
    };
    const get = await run({
        name: 'read route',
        answers: [{ match: '/GET/', status: 200, body: MISS }, { match: read, status: 200, body: ROWS }],
        calls: [CACHE_READ, 'GET ' + read + PATH + '?' + QUERY],
        ...common,
        source: 'upstream',
    });
    const post = await run({
        name: 'write route',
        method: 'POST',
        answers: [{ match: read, status: 200, body: ROWS }],
        calls: ['POST ' + read + PATH + '?' + QUERY],
        ...common,
    });

    const head = await run({
        name: 'head route',
        method: 'HEAD',
        answers: [{ match: read, status: 200, body: ROWS }],
        calls: ['HEAD ' + read + PATH + '?' + QUERY],
        ...common,
    });

    assertCalls(get.calls, [CACHE_READ, 'GET ' + read + PATH + '?' + QUERY, CACHE_WRITE]);
    assertCalls(head.calls, ['HEAD ' + read + PATH + '?' + QUERY]);
    assertCalls(post.calls, ['POST ' + read + PATH + '?' + QUERY]);
});

test('proxy: keys media types correctly in the cache', async () => {
    const answers = [{ match: '/GET/', status: 200, body: HIT }];
    const result = { status: 200, body: '{"cached":true}', contentType: JSON_CT, xCache: 'HIT', source: 'cache', events: [], calls: [CACHE_READ] };

    const bare = await run({ name: 'bare', accept: 'application/json', answers: answers, ...result });
    const parameters = await run({ name: 'parameters', accept: JSON_CT, answers: answers, ...result });
    const wildcard = await run({ name: 'wildcard', accept: '*/*', answers: answers, ...result });
    const csv = await run({ name: 'csv', accept: 'text/csv', answers: answers, ...result });

    assert.equal(bare.calls[0], parameters.calls[0], 'parameters must not split the key');
    assert.equal(bare.calls[0], wildcard.calls[0], 'a wildcard must use the default media type');
    assert.notEqual(bare.calls[0], csv.calls[0], 'a different media type must use a different key');
});

test('proxy: separates the cache by the pinned snapshot version', async () => {
    const answers = [{ match: '/GET/', status: 200, body: HIT }];
    const result = { status: 200, body: '{"cached":true}', contentType: JSON_CT, xCache: 'HIT', source: 'cache', events: [], calls: [CACHE_READ] };

    const latest = await run({ name: 'latest', answers: answers, ...result });
    const pinned = await run({ name: 'pinned', snapshot: '5', answers: answers, ...result });
    const other = await run({ name: 'other', snapshot: '6', answers: answers, ...result });

    assert.notEqual(latest.calls[0], pinned.calls[0], 'a pinned version must not reuse the latest entry');
    assert.notEqual(pinned.calls[0], other.calls[0], 'two versions must not share an entry');
});

test('proxy: shares an entry between tokens with the same claims', async () => {
    const answers = [{ match: '/GET/', status: 200, body: HIT }];
    const result = { status: 200, body: '{"cached":true}', contentType: JSON_CT, xCache: 'HIT', source: 'cache', events: [], calls: [CACHE_READ] };

    const first = await run({ name: 'first', token: tokenFor({ sub: 'alice' }), answers: answers, ...result });
    const refreshed = await run({ name: 'refreshed', token: tokenFor({ sub: 'alice' }, 'other'), answers: answers, ...result });

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
        contentType: JSON_CT,
        xCache: 'MISS',
        source: 'upstream',
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

    const backend = upstream.calls.filter((call) => call.indexOf(CACHE_READ_ROOT) === -1 && call.indexOf(CACHE_WRITE_ROOT) === -1);

    assert.equal(backend.length, 1, 'two concurrent requests must reach the upstream once');
});

test('proxy: avoids the globals and methods that the engine does not provide', async () => {
    const source = readFileSync(fileURLToPath(new URL('./proxy.ts', import.meta.url)), 'utf8');
    const unsupported = [
        'Map', 'Set', 'WeakMap', 'WeakSet', 'Proxy', 'Reflect', 'Symbol',
        'filter', 'find', 'findIndex', 'flat', 'flatMap', 'reduce', 'reduceRight', 'includes',
    ];

    unsupported.forEach((name) => {
        const pattern = new RegExp('\\b' + name + '\\b');

        assert.doesNotMatch(source, pattern, 'the njs engine does not provide ' + name);
    });
});

test('proxy: serves a stored answer without the headers of the live answer', async () => {
    const outcome = await run({
        name: 'stored answer',
        answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200, body: '{"cached":true}', contentType: JSON_CT,
        xCache: 'HIT', source: 'cache', events: [], calls: [CACHE_READ],
    });

    assert.equal(outcome.headers['Content-Range'], undefined, 'a cached answer carries no range header');
});

test('proxy: separates the cache by the token claims', async () => {
    const alice = await run({
        name: 'alice', token: tokenFor({ sub: 'alice' }),
        answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200, body: '{"cached":true}', contentType: JSON_CT,
        xCache: 'HIT', source: 'cache', events: [], calls: [CACHE_READ],
    });

    const bob = await run({
        name: 'bob', token: tokenFor({ sub: 'bob' }),
        answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200, body: '{"cached":true}', contentType: JSON_CT,
        xCache: 'HIT', source: 'cache', events: [], calls: [CACHE_READ],
    });

    assert.notEqual(alice.calls[0], bob.calls[0], 'two subjects must not share a cache key');
});

test('proxy: separates the cache by the profile', async () => {
    const withProfile = await run({
        name: 'with profile', answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200, body: '{"cached":true}', contentType: JSON_CT,
        xCache: 'HIT', source: 'cache', events: [], calls: [CACHE_READ],
    });

    const withoutProfile = await run({
        name: 'without profile', profile: '', answers: [{ match: '/GET/', status: 200, body: HIT }],
        status: 200, body: '{"cached":true}', contentType: JSON_CT,
        xCache: 'HIT', source: 'cache', events: [], calls: [CACHE_READ],
    });

    assert.notEqual(withProfile.calls[0], withoutProfile.calls[0], 'the profile must be part of the key');
});

test('proxy: treats malformed tokens as anonymous', async () => {
    const cases: { name: string, token: string }[] = [
        { name: 'missing header', token: '' },
        { name: 'not a bearer', token: 'Basic abc' },
        { name: 'no payload', token: 'Bearer not-a-token' },
        { name: 'empty claims', token: tokenFor({}) },
        { name: 'invalid base64', token: 'Bearer header.!!!' },
    ];

    for (const item of cases) {
        const outcome = await run({
            name: item.name,
            token: item.token,
            answers: MISS_UPSTREAM_STORE,
            status: 200,
            body: ROWS,
            contentType: JSON_CT,
            xCache: 'MISS',
            source: 'upstream',
            events: [],
            calls: MISS_FLOW_CALLS,
        });

        assert.equal(outcome.status, 200, item.name + ': status differs');
        assert.equal(outcome.body, ROWS, item.name + ': body differs');
    }
});

test('proxy: reports an unexpected handler exception as a structured warning', async () => {
    const scenario = SCENARIOS[0];
    const upstream = fakeUpstream(scenario);
    setGlobals(scenario, upstream.fetch);

    const warnings: string[] = [];
    const response: { status: number | null, body: string | null } = { status: null, body: null };
    const request = {
        uri: PATH,
        method: 'GET',
        headersIn: { Authorization: {} },
        requestText: '',
        headersOut: {},
        variables: { args: QUERY, fallback_pgrst: UPSTREAM, proxy_cache_ttl: '300', proxy_max_body: '' },
        log: () => { },
        warn: (message: string) => { warnings.push(message); },
        return: (status: number, body: string) => { response.status = status; response.body = body; },
    };

    await proxy.handle(request as unknown as NginxHTTPRequest);

    assert.equal(response.status, 502);
    assert.equal(response.body, '{"error":"proxy exception"}');
    assert.equal(warnings.length, 1);
    assert.equal(parseLine(warnings[0], 'exception').event, 'exception');
});
