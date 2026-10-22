import { describe, expect, it } from 'vitest';
import type { LogLine } from '../types';
import { follow, frame, type Event, type Source } from './logstream';

const line = (position: number, loggedAt: string, workflow = 'w1'): LogLine => ({
	workflow_id: workflow,
	position,
	logged_at: loggedAt,
	level: 'INFO',
	message: `line ${position}`,
	fields: {},
	schema_name: null,
	table_name: null,
	run_id: 'r1'
});

/** A source that answers from a script: each tick reads the next status and the next batch of lines. */
function scripted(ticks: { status: string; lines: LogLine[] }[]) {
	let tick = -1;
	const asked: (string | null)[] = [];
	const source: Source = {
		status: () => {
			tick = Math.min(tick + 1, ticks.length - 1);
			return Promise.resolve(ticks[tick]?.status ?? 'SUCCESS');
		},
		lines: (after) => {
			asked.push(after);
			return Promise.resolve(ticks[tick]?.lines ?? []);
		}
	};

	return { source, asked };
}

async function collect(events: AsyncIterable<Event>): Promise<Event[]> {
	const all: Event[] = [];

	for await (const event of events) {
		all.push(event);
	}

	return all;
}

const options = (extra: Partial<Parameters<typeof follow>[1]> = {}) => ({
	after: null,
	signal: new AbortController().signal,
	wait: () => Promise.resolve(),
	now: () => 0,
	...extra
});

describe('follow', () => {
	it('sends each line once, reads from the last time, and ends after the run finishes', async () => {
		const { source, asked } = scripted([
			{ status: 'PENDING', lines: [line(0, '2026-01-01T00:00:01Z')] },
			{ status: 'PENDING', lines: [line(0, '2026-01-01T00:00:01Z'), line(1, '2026-01-01T00:00:02Z')] },
			{ status: 'SUCCESS', lines: [line(1, '2026-01-01T00:00:02Z'), line(2, '2026-01-01T00:00:03Z')] }
		]);

		const events = await collect(follow(source, options()));

		expect(events).toEqual([
			{ type: 'status', status: 'PENDING' },
			{ type: 'lines', lines: [line(0, '2026-01-01T00:00:01Z')] },
			{ type: 'lines', lines: [line(1, '2026-01-01T00:00:02Z')] },
			{ type: 'status', status: 'SUCCESS' },
			{ type: 'lines', lines: [line(2, '2026-01-01T00:00:03Z')] },
			{ type: 'done' }
		]);
		expect(asked).toEqual([null, '2026-01-01T00:00:01Z', '2026-01-01T00:00:02Z']);
	});

	it('starts from the time that the client gives', async () => {
		const { source, asked } = scripted([{ status: 'SUCCESS', lines: [] }]);

		await collect(follow(source, options({ after: '2026-01-01T00:00:09Z' })));

		expect(asked).toEqual(['2026-01-01T00:00:09Z']);
	});

	it('keeps the stream open while a finished status may still change', async () => {
		const { source } = scripted([
			{ status: 'CANCELLED', lines: [] },
			{ status: 'CANCELLED', lines: [] },
			{ status: 'ENQUEUED', lines: [] },
			{ status: 'SUCCESS', lines: [] }
		]);
		let clock = 0;

		const events = await collect(
			follow(
				source,
				options({
					settleMs: 10_000,
					now: () => clock,
					wait: () => {
						clock += 1_000;
						return Promise.resolve();
					}
				})
			)
		);

		expect(events.map((event) => (event.type === 'status' ? event.status : event.type))).toEqual([
			'CANCELLED',
			'ENQUEUED',
			'SUCCESS',
			'done'
		]);
	});

	it('stops without a done event when the client leaves', async () => {
		const controller = new AbortController();
		const { source } = scripted([{ status: 'PENDING', lines: [] }]);

		const events = await collect(
			follow(
				source,
				options({
					signal: controller.signal,
					wait: () => {
						controller.abort();
						return Promise.resolve();
					}
				})
			)
		);

		expect(events).toEqual([{ type: 'status', status: 'PENDING' }]);
	});

	it('passes on an error of the source', async () => {
		const source: Source = {
			status: () => Promise.reject(new Error('PostgREST is down')),
			lines: () => Promise.resolve([])
		};

		await expect(collect(follow(source, options()))).rejects.toThrow('PostgREST is down');
	});
});

describe('frame', () => {
	it('writes an event with its data on one line', () => {
		expect(frame({ type: 'status', status: 'ERROR' })).toBe('event: status\ndata: {"status":"ERROR"}\n\n');
	});

	it('gives a lines event the time of its last line as id, so a reconnect continues from there', () => {
		const text = frame({ type: 'lines', lines: [line(0, '2026-01-01T00:00:01Z'), line(1, '2026-01-01T00:00:02Z')] });

		expect(text.startsWith('event: lines\nid: 2026-01-01T00:00:02Z\ndata: ')).toBe(true);
		expect(text.endsWith('\n\n')).toBe(true);
	});

	it('writes a done event without data', () => {
		expect(frame({ type: 'done' })).toBe('event: done\ndata: {}\n\n');
	});
});
