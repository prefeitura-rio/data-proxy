import { describe, expect, it } from 'vitest';
import { lastLoggedAt, mergeLogs } from './logs';
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
