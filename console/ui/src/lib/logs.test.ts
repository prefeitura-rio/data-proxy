import { describe, expect, it } from 'vitest';
import { collectLines, lastLoggedAt, mergeLogs } from './logs';
import type { LogLine } from './types';

const line = (workflow_id: string, position: number, logged_at: string): LogLine => ({
	workflow_id,
	position,
	logged_at,
	level: 'INFO',
	message: `${workflow_id}:${position}`,
	fields: null,
	schema_name: null,
	table_name: null,
	run_id: 'run'
});

describe('mergeLogs', () => {
	it('drops a line that it already has, by workflow and position', () => {
		const known = [line('a', 0, '2026-01-01T00:00:01Z')];
		const fresh = [line('a', 0, '2026-01-01T00:00:01Z'), line('a', 1, '2026-01-01T00:00:02Z')];

		expect(mergeLogs(known, fresh).map((l) => l.message)).toEqual(['a:0', 'a:1']);
	});

	it('keeps lines of two workflows that share a position', () => {
		const merged = mergeLogs([line('a', 0, '2026-01-01T00:00:01Z')], [line('b', 0, '2026-01-01T00:00:01Z')]);

		expect(merged).toHaveLength(2);
	});

	it('orders the lines by time, then by workflow and position', () => {
		const merged = mergeLogs(
			[],
			[line('b', 1, '2026-01-01T00:00:02Z'), line('a', 0, '2026-01-01T00:00:01Z'), line('a', 1, '2026-01-01T00:00:02Z')]
		);

		expect(merged.map((l) => l.message)).toEqual(['a:0', 'a:1', 'b:1']);
	});

	it('returns the same lines when nothing is new', () => {
		const known = [line('a', 0, '2026-01-01T00:00:01Z')];

		expect(mergeLogs(known, [])).toEqual(known);
	});
});

describe('lastLoggedAt', () => {
	it('is the time of the newest line, or nothing without lines', () => {
		expect(lastLoggedAt([])).toBeNull();
		expect(lastLoggedAt([line('a', 0, '2026-01-01T00:00:01Z'), line('a', 1, '2026-01-01T00:00:05Z')])).toBe(
			'2026-01-01T00:00:05Z'
		);
	});
});

describe('collectLines', () => {
	const pages = (all: number[], cap: number) => (limit: number, offset: number) =>
		Promise.resolve({ rows: all.slice(offset, offset + Math.min(limit, cap)), total: all.length });
	const numbers = (count: number) => Array.from({ length: count }, (_, index) => index);

	it.each([
		{ name: 'fewer lines than asked', all: 30, cap: 1000, wanted: 100, expected: 30 },
		{ name: 'exactly the lines asked', all: 100, cap: 1000, wanted: 100, expected: 100 },
		{ name: 'more lines than asked', all: 5200, cap: 5000, wanted: 5000, expected: 5000 },
		{ name: 'a server that returns at most 1000 rows', all: 5200, cap: 1000, wanted: 5000, expected: 5000 },
		{ name: 'a server that returns at most 7 rows', all: 20, cap: 7, wanted: 15, expected: 15 }
	])('returns $expected lines for $name', async ({ all, cap, wanted, expected }) => {
		const result = await collectLines(pages(numbers(all), cap), wanted);

		expect(result.rows).toEqual(numbers(all).slice(0, expected));
		expect(result.total).toBe(all);
	});

	it('stops at an empty page when the server gives no total', async () => {
		const calls: number[] = [];
		const result = await collectLines((_limit, offset) => {
			calls.push(offset);

			return Promise.resolve({ rows: offset === 0 ? [1, 2, 3] : [], total: null });
		}, 10);

		expect(result.rows).toEqual([1, 2, 3]);
		expect(calls).toEqual([0, 3]);
	});

	it('asks for no page when no line is wanted', async () => {
		const result = await collectLines(() => Promise.resolve({ rows: [1], total: 1 }), 0);

		expect(result).toEqual({ rows: [], total: null });
	});
});
