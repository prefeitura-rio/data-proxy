import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { followRun, type Follower } from './follow';

vi.mock('$app/paths', () => ({
	resolve: (_route: string, params: { id: string }) => `/console/sync/runs/${params.id}/logs`
}));

type Listener = (event: { data: string }) => void;

class FakeEventSource {
	static readonly CLOSED = 2;
	static last: FakeEventSource | undefined;

	readyState = 1;
	onopen: (() => void) | null = null;
	onerror: (() => void) | null = null;
	closed = false;
	readonly listeners = new Map<string, Listener>();

	constructor(readonly address: string) {
		FakeEventSource.last = this;
	}

	addEventListener(name: string, listener: Listener) {
		this.listeners.set(name, listener);
	}

	close() {
		this.closed = true;
	}

	emit(name: string, data: unknown) {
		this.listeners.get(name)?.({ data: JSON.stringify(data) });
	}
}

const line = (position: number) => ({
	workflow_id: 'w',
	position,
	logged_at: '2026-01-01T00:00:00Z',
	level: 'INFO',
	message: `line ${position}`,
	fields: null,
	schema_name: null,
	table_name: null,
	run_id: 'run'
});

function follower(): Follower & { events: string[] } {
	const events: string[] = [];

	return {
		events,
		onLines: (lines) => {
			events.push(`lines:${lines.map((item) => item.position).join(',')}`);
		},
		onStatus: (status) => {
			events.push(`status:${status}`);
		},
		onState: (state) => {
			events.push(`state:${state}`);
		},
		onEnd: () => {
			events.push('end');
		}
	};
}

const source = () => {
	const found = FakeEventSource.last;

	if (found === undefined) {
		throw new Error('The run opened no stream');
	}

	return found;
};

describe('followRun', () => {
	beforeEach(() => {
		vi.stubGlobal('EventSource', FakeEventSource);
	});

	afterEach(() => {
		vi.unstubAllGlobals();
		FakeEventSource.last = undefined;
	});

	it('opens the stream of the run, from the time of the last line, and settles when it is asked to', () => {
		followRun('run-1', { after: '2026-01-01T00:00:00Z', settle: true }, follower());

		expect(source().address).toBe('/console/sync/runs/run-1/logs?after=2026-01-01T00%3A00%3A00Z&settle=1');
	});

	it('sends no parameter when the page has no line and does not settle', () => {
		followRun('run-1', { after: null, settle: false }, follower());

		expect(source().address).toBe('/console/sync/runs/run-1/logs?');
	});

	it('passes the lines and the status of an event to the follower', () => {
		const watcher = follower();

		followRun('run-1', { after: null, settle: false }, watcher);
		source().emit('lines', [line(1), line(2)]);
		source().emit('status', { status: 'SUCCESS' });

		expect(watcher.events).toEqual(['lines:1,2', 'status:SUCCESS']);
	});

	it('closes the stream and ends when the run says that it is done', () => {
		const watcher = follower();

		followRun('run-1', { after: null, settle: false }, watcher);
		source().emit('done', {});

		expect(source().closed).toBe(true);
		expect(watcher.events).toEqual(['end']);
	});

	it('says that it is live when the stream opens, and reconnecting after a drop that the browser retries', () => {
		const watcher = follower();

		followRun('run-1', { after: null, settle: false }, watcher);
		source().onopen?.();
		source().onerror?.();

		expect(watcher.events).toEqual(['state:live', 'state:reconnecting']);
	});

	it('ends when the server closed the stream for good', () => {
		const watcher = follower();

		followRun('run-1', { after: null, settle: false }, watcher);
		source().readyState = FakeEventSource.CLOSED;
		source().onerror?.();

		expect(watcher.events).toEqual(['end']);
	});

	it('returns the function that closes the stream', () => {
		const stop = followRun('run-1', { after: null, settle: false }, follower());

		stop();

		expect(source().closed).toBe(true);
	});

	it('refuses a line that does not match the schema', () => {
		followRun('run-1', { after: null, settle: false }, follower());

		expect(() => {
			source().emit('lines', [{ position: 'one' }]);
		}).toThrow();
	});
});
