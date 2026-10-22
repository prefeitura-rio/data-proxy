import * as v from 'valibot';
import { parseTotal } from '../list';

export type Result<T> = { ok: true; data: T } | { ok: false; status: number; message: string };

const MESSAGES: Record<number, (detail: string) => string> = {
	400: (detail) => `The value is wrong: ${detail}`,
	401: () => 'Sign in again.',
	403: () => "You aren't allowed to do this.",
	409: (detail) => `The row clashes: ${detail}`
};

const ErrorBody = v.object({ message: v.string() });

async function detailOf(response: Response): Promise<string> {
	const body = v.safeParse(ErrorBody, await response.json().catch(() => null));

	return body.success ? body.output.message : '';
}

export async function toResult<T>(response: Response, schema: v.GenericSchema<unknown, T>): Promise<Result<T>> {
	if (response.ok) {
		const parsed = v.safeParse(schema, response.status === 204 ? null : await response.json());

		return parsed.success
			? { ok: true, data: parsed.output }
			: {
					ok: false,
					status: 502,
					message: `The database answered with an unexpected shape: ${v.summarize(parsed.issues)}`
				};
	}

	const detail = await detailOf(response);
	const message = (MESSAGES[response.status] ?? ((text: string) => `The database failed: ${text}`))(detail);

	return { ok: false, status: response.status, message };
}

export function requestHeaders(token: string, options: { profile: string; write: boolean; count?: boolean }): Headers {
	const headers = new Headers({
		Authorization: `Bearer ${token}`,
		'Accept-Profile': options.profile
	});

	if (options.write) {
		headers.set('Content-Profile', options.profile);
		headers.set('Content-Type', 'application/json');
		headers.set('Prefer', 'return=representation');
	} else if (options.count) {
		headers.set('Prefer', 'count=exact');
	}

	return headers;
}

export function postgrest(baseUrl: string, token: string, fetchFn: typeof fetch = fetch) {
	async function send<T>(
		method: string,
		path: string,
		schema: v.GenericSchema<unknown, T>,
		body?: unknown,
		profile = 'console'
	): Promise<Result<T>> {
		const write = method !== 'GET';
		const response = await fetchFn(`${baseUrl}${path}`, {
			method,
			headers: requestHeaders(token, { profile, write }),
			body: body === undefined ? undefined : JSON.stringify(body)
		});

		return toResult(response, schema);
	}

	async function page<T>(
		path: string,
		schema: v.GenericSchema<unknown, T>,
		profile = 'console'
	): Promise<Result<{ rows: T; total: number | null }>> {
		const response = await fetchFn(`${baseUrl}${path}`, {
			method: 'GET',
			headers: requestHeaders(token, { profile, write: false, count: true })
		});
		const total = parseTotal(response.headers.get('Content-Range'));
		const result = await toResult(response, schema);

		return result.ok ? { ok: true, data: { rows: result.data, total } } : result;
	}

	const any = v.unknown();

	return {
		get: <T>(path: string, schema: v.GenericSchema<unknown, T>, profile?: string) =>
			send('GET', path, schema, undefined, profile),
		page,
		post: (path: string, body: unknown, profile?: string) => send('POST', path, any, body, profile),
		patch: (path: string, body: unknown, profile?: string) => send('PATCH', path, any, body, profile),
		delete: (path: string, profile?: string) => send('DELETE', path, any, undefined, profile),
		rpc: <T>(name: string, schema: v.GenericSchema<unknown, T>, body?: unknown, profile?: string) =>
			send('POST', `/rpc/${name}`, schema, body ?? {}, profile)
	};
}
