import { describe, expect, it, vi } from 'vitest';
import { need } from './api';

vi.mock('./settings', () => ({ settings: () => ({ postgrestUrl: 'http://postgrest.test' }) }));

const refusal = (status: number) => ({ ok: false as const, status, message: `refused ${status}` });

function statusOf(call: () => unknown): number {
	try {
		call();
	} catch (error) {
		return typeof error === 'object' && error !== null && 'status' in error ? Number(error.status) : -1;
	}

	return 0;
}

describe('need', () => {
	it('returns the data of a read', () => {
		expect(need({ ok: true, data: [1, 2] })).toEqual([1, 2]);
	});

	it.each([
		['a missing row', 404, 404],
		['a refusal of the token', 403, 403],
		['a failure of the database', 500, 500],
		['a status that is not an error', 302, 500],
		['a status beyond the HTTP range', 700, 500]
	])('stops the page for %s with the right status', (_name, status, expected) => {
		expect(statusOf(() => need(refusal(status)))).toBe(expected);
	});
});
