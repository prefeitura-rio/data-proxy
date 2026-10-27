import { describe, expect, it } from 'vitest';
import { atBottom, collectLines, filterLogs, lastLoggedAt, levelsOf, lineText, mergeLogs, tablesOf } from './logs';
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

describe('filterLogs', () => {
	const lines: LogLine[] = [
		{ ...line('a', 0, '2026-01-01T00:00:01Z'), level: 'INFO', message: 'Loaded table Events' },
		{ ...line('a', 1, '2026-01-01T00:00:02Z'), level: 'ERROR', message: 'Upload failed' },
		{ ...line('a', 2, '2026-01-01T00:00:03Z'), level: 'ERROR', message: 'Retry of events' }
	];

	it.each([
		['no filter', { level: '', table: '', text: '' }, 3],
		['a level', { level: 'ERROR', table: '', text: '' }, 2],
		['a text that ignores the case', { level: '', table: '', text: 'EVENTS' }, 2],
		['a level and a text', { level: 'ERROR', table: '', text: 'events' }, 1],
		['a text that no line has', { level: '', table: '', text: 'nothing' }, 0]
	])('keeps the lines for %s', (_name, filter, count) => {
		expect(filterLogs(lines, filter)).toHaveLength(count);
	});

	it('keeps the lines of one table', () => {
		const events = { ...line('a', 0, '2026-01-01T00:00:01Z'), table_name: 'app.events' };
		const people = { ...line('a', 1, '2026-01-01T00:00:02Z'), table_name: 'app.people' };

		expect(filterLogs([events, people], { level: '', table: 'app.people', text: '' })).toEqual([people]);
	});

	it('matches the text against a value of a field', () => {
		const counted = { ...line('a', 0, '2026-01-01T00:00:01Z'), fields: { rows: 48210 } };
		const plain = line('a', 1, '2026-01-01T00:00:02Z');

		expect(filterLogs([counted, plain], { level: '', table: '', text: '48210' })).toEqual([counted]);
	});

	it('lists each level once, in order', () => {
		expect(levelsOf(lines)).toEqual(['ERROR', 'INFO']);
	});
});

describe('atBottom', () => {
	it.each([
		['at the end', { scrollTop: 600, clientHeight: 400, scrollHeight: 1000 }, true],
		['a few pixels from the end', { scrollTop: 590, clientHeight: 400, scrollHeight: 1000 }, true],
		['far from the end', { scrollTop: 100, clientHeight: 400, scrollHeight: 1000 }, false]
	])('says whether the reader is %s', (_name, box, expected) => {
		expect(atBottom(box)).toBe(expected);
	});
});

describe('lineText', () => {
	const base = line('a', 0, '2026-10-25T12:00:03Z');

	it.each([
		['no context and no fields', {}, '2026-10-25T12:00:03Z INFO a:0'],
		['a schema and a table', { schema_name: 'app', table_name: 'events' }, '2026-10-25T12:00:03Z INFO app.events a:0'],
		['a table without a schema', { table_name: 'events' }, '2026-10-25T12:00:03Z INFO events a:0'],
		[
			'fields',
			{ message: 'Partition written', fields: { part: 2, of: 12, path: 's3://b/t' } },
			'2026-10-25T12:00:03Z INFO Partition written: part=2 of=12 path=s3://b/t'
		],
		['empty fields', { fields: {} }, '2026-10-25T12:00:03Z INFO a:0']
	])('writes a line with %s', (_name, change, expected) => {
		expect(lineText({ ...base, ...change })).toBe(expected);
	});
});

describe('tablesOf', () => {
	it('lists each table once, in order, and skips a line without a table', () => {
		const lines = [
			{ ...line('a', 0, '1'), table_name: 'b.t' },
			{ ...line('a', 1, '2'), table_name: null },
			{ ...line('a', 2, '3'), table_name: 'a.t' },
			{ ...line('a', 3, '4'), table_name: 'b.t' }
		];

		expect(tablesOf(lines)).toEqual(['a.t', 'b.t']);
	});
});
