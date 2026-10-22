import { describe, expect, it } from 'vitest';
import { controlsFor, runFilters, RUN_STATUSES } from './runs';

describe('runFilters', () => {
	it.each([
		['no filter', '', ''],
		['a status', 'status=ERROR', '&status=eq.ERROR'],
		['a schema', 'schema=test', '&schemas=cs.%7Btest%7D'],
		['a start date', 'from=2026-10-01', '&created_at=gte.2026-10-01'],
		['an end date, which includes the day', 'to=2026-10-31', '&created_at=lt.2026-11-01'],
		['an id prefix', 'id=4a1f-9', '&id=like.4a1f-9*'],
		[
			'every filter',
			'status=SUCCESS&schema=pic&from=2026-01-01&to=2026-01-31&id=ab',
			'&status=eq.SUCCESS&schemas=cs.%7Bpic%7D&created_at=gte.2026-01-01&created_at=lt.2026-02-01&id=like.ab*'
		]
	])('builds %s', (_, query, expected) => {
		expect(runFilters(new URLSearchParams(query))).toBe(expected);
	});

	it.each([
		['an unknown status', 'status=DROP'],
		['a schema with a quote', "schema=a'b"],
		['a schema with uppercase letters', 'schema=Test'],
		['a date that is not a date', 'from=yesterday'],
		['an impossible date', 'to=2026-13-40'],
		['an id with a wildcard', 'id=*'],
		['an id with an operator', 'id=a,b']
	])('ignores %s', (_, query) => {
		expect(runFilters(new URLSearchParams(query))).toBe('');
	});

	it('lists the statuses of DBOS', () => {
		expect(RUN_STATUSES).toContain('ENQUEUED');
		expect(RUN_STATUSES).toContain('CANCELLED');
	});
});

describe('controlsFor', () => {
	const both = ['control_sync', 'start_sync'];

	it.each([
		['an enqueued run', 'ENQUEUED', both, { cancel: true, resume: false, retry: false }],
		['a pending run', 'PENDING', both, { cancel: true, resume: false, retry: false }],
		['a delayed run', 'DELAYED', both, { cancel: true, resume: false, retry: false }],
		['a cancelled run', 'CANCELLED', both, { cancel: false, resume: true, retry: true }],
		[
			'a run that ran out of attempts',
			'MAX_RECOVERY_ATTEMPTS_EXCEEDED',
			both,
			{ cancel: false, resume: true, retry: true }
		],
		['a finished run', 'SUCCESS', both, { cancel: false, resume: false, retry: true }],
		['a failed run', 'ERROR', both, { cancel: false, resume: false, retry: true }],
		[
			'a cancelled run without the start permission',
			'CANCELLED',
			['control_sync'],
			{ cancel: false, resume: true, retry: false }
		],
		[
			'an enqueued run without the control permission',
			'ENQUEUED',
			['start_sync', 'read_sync'],
			{ cancel: false, resume: false, retry: false }
		],
		['a failed run without any permission', 'ERROR', [], { cancel: false, resume: false, retry: false }],
		['a status that DBOS may add later', 'WAITING', both, { cancel: false, resume: false, retry: false }]
	])('offers the right buttons for %s', (_, status, permissions, expected) => {
		expect(controlsFor(status, permissions)).toEqual(expected);
	});
});
