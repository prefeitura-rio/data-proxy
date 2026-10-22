import { describe, expect, it } from 'vitest';
import * as v from 'valibot';
import { urlOf } from './fetch-url';
import { postgrest, requestHeaders, toResult } from './postgrest';

const json = (body: unknown, status: number) => new Response(JSON.stringify(body), { status });

describe('toResult', () => {
	it.each([
		{ status: 200, body: [{ id: 1 }], ok: true },
		{ status: 201, body: [{ id: 1 }], ok: true }
	])('returns the data of a $status', async ({ status, body }) => {
		expect(await toResult(json(body, status), v.unknown())).toEqual({ ok: true, data: body });
	});

	it('returns null data for a 204', async () => {
		expect(await toResult(new Response(null, { status: 204 }), v.unknown())).toEqual({ ok: true, data: null });
	});

	it.each([
		{ status: 400, message: 'The value is wrong: bad cron' },
		{ status: 401, message: 'Sign in again.' },
		{ status: 403, message: "You aren't allowed to do this." },
		{ status: 409, message: 'The row clashes: duplicate key' },
		{ status: 500, message: 'The database failed: boom' }
	])('maps a $status to a message', async ({ status, message }) => {
		const result = await toResult(
			json({ message: status === 400 ? 'bad cron' : status === 409 ? 'duplicate key' : 'boom' }, status),
			v.unknown()
		);

		expect(result).toEqual({ ok: false, status, message });
	});

	it('survives a body that is not JSON', async () => {
		const result = await toResult(new Response('<html>', { status: 502 }), v.unknown());

		expect(result).toMatchObject({ ok: false, status: 502 });
	});
});

describe('requestHeaders', () => {
	it('sends the token and the profile of a read', () => {
		const headers = requestHeaders('tok', { profile: 'console', write: false });

		expect(headers.get('Authorization')).toBe('Bearer tok');
		expect(headers.get('Accept-Profile')).toBe('console');
		expect(headers.has('Content-Profile')).toBe(false);
		expect(headers.has('Prefer')).toBe(false);
	});

	it('sends the content profile and asks for the row back on a write', () => {
		const headers = requestHeaders('tok', { profile: 'sales', write: true });

		expect(headers.get('Content-Profile')).toBe('sales');
		expect(headers.get('Accept-Profile')).toBe('sales');
		expect(headers.get('Prefer')).toBe('return=representation');
		expect(headers.get('Content-Type')).toBe('application/json');
	});
});

describe('postgrest client', () => {
	it('calls the path with the headers and parses the answer', async () => {
		const seen: { url: string; init?: RequestInit }[] = [];
		const client = postgrest('http://api', 'tok', (url, init) => {
			seen.push({ url: urlOf(url), init });

			return Promise.resolve(json([{ name: 'a' }], 200));
		});

		const result = await client.get('/schemas?order=name', v.array(v.object({ name: v.string() })));

		expect(result).toEqual({ ok: true, data: [{ name: 'a' }] });
		expect(seen[0]?.url).toBe('http://api/schemas?order=name');
		expect(new Headers(seen[0]?.init?.headers).get('Authorization')).toBe('Bearer tok');
	});

	it('writes a body as JSON with the method', async () => {
		let init: RequestInit | undefined;
		const client = postgrest('http://api', 'tok', (_url, options) => {
			init = options;

			return Promise.resolve(json([], 201));
		});

		await client.post('/rls', { column_name: 'x' });

		expect(init?.method).toBe('POST');
		expect(init?.body).toBe('{"column_name":"x"}');
	});

	it('calls a function with the POST method and the body', async () => {
		let seen = '';
		const client = postgrest('http://api', 'tok', (url) => {
			seen = urlOf(url);

			return Promise.resolve(json('run-1', 200));
		});

		expect(await client.rpc('start_sync', v.string(), { schemas: ['sales'], tables: [] })).toEqual({
			ok: true,
			data: 'run-1'
		});
		expect(seen).toBe('http://api/rpc/start_sync');
	});
});

describe('postgrest client page', () => {
	const rows = v.array(v.object({ n: v.number() }));

	it('asks for the count and returns the rows with the total', async () => {
		let prefer = '';
		const client = postgrest('http://api', 'tok', (_url, init) => {
			prefer = new Headers(init?.headers).get('Prefer') ?? '';

			return Promise.resolve(
				new Response(JSON.stringify([{ n: 1 }]), { status: 206, headers: { 'Content-Range': '0-0/42' } })
			);
		});

		expect(await client.page('/runs?limit=1', rows)).toEqual({ ok: true, data: { rows: [{ n: 1 }], total: 42 } });
		expect(prefer).toBe('count=exact');
	});

	it('returns a null total when the header is missing', async () => {
		const client = postgrest('http://api', 'tok', () => Promise.resolve(json([{ n: 1 }], 200)));

		expect(await client.page('/runs', rows)).toEqual({ ok: true, data: { rows: [{ n: 1 }], total: null } });
	});

	it('returns the refusal of the database', async () => {
		const client = postgrest('http://api', 'tok', () => Promise.resolve(json({ message: 'no' }, 403)));

		expect(await client.page('/runs', rows)).toMatchObject({ ok: false, status: 403 });
	});
});

describe('toResult with a schema', () => {
	it.each([
		{ name: 'a body of the wrong type', body: { id: 'x' } },
		{ name: 'a body that lacks a field', body: [{ other: 1 }] }
	])('answers 502 for $name', async ({ body }) => {
		const result = await toResult(json(body, 200), v.array(v.object({ id: v.number() })));

		expect(result).toMatchObject({ ok: false, status: 502 });
	});
});
