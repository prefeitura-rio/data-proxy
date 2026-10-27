import type { LogLine } from '../types';

export type Event = { type: 'status'; status: string } | { type: 'lines'; lines: LogLine[] } | { type: 'done' };

/** What the stream reads: the status of the run, and the log lines from a time on. The lines may repeat. */
export type Source = {
	status: () => Promise<string>;
	lines: (after: string | null) => Promise<LogLine[]>;
};

export type FollowOptions = {
	/** The time of the last line that the client has. */
	after: string | null;
	signal: AbortSignal;
	/** Wait for the next read. */
	wait: (signal: AbortSignal) => Promise<unknown>;
	now: () => number;
	/** Keep the stream open this long even if the run is finished, because a resume or a retry is on its way. */
	settleMs?: number;
};

const FINISHED = ['SUCCESS', 'ERROR', 'CANCELLED', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'];

const keyOf = (line: LogLine) => `${line.workflow_id}:${line.position}`;

/**
 * Follow a run: say its status when it changes, and send each new log line once. The status is read before the
 * lines, so the lines that come with a finished status are the last ones, and the stream can end.
 */
export async function* follow(source: Source, options: FollowOptions): AsyncGenerator<Event> {
	const started = options.now();
	const seen = new Set<string>();
	let cursor = options.after;
	let known: string | null = null;

	while (!options.signal.aborted) {
		const status = await source.status();
		const fresh = (await source.lines(cursor)).filter((line) => !seen.has(keyOf(line)));

		if (status !== known) {
			known = status;
			yield { type: 'status', status };
		}

		if (fresh.length > 0) {
			for (const line of fresh) {
				seen.add(keyOf(line));
			}

			cursor = fresh.map((line) => line.logged_at).reduce((a, b) => (a > b ? a : b), cursor ?? '');
			yield { type: 'lines', lines: fresh };
		}

		if (FINISHED.includes(status) && options.now() - started >= (options.settleMs ?? 0)) {
			yield { type: 'done' };
			return;
		}

		await options.wait(options.signal);
	}
}

/** The text of one server-sent event. A lines event carries the time of its last line as its id. */
export function frame(event: Event): string {
	switch (event.type) {
		case 'status':
			return `event: status\ndata: ${JSON.stringify({ status: event.status })}\n\n`;
		case 'lines':
			return `event: lines\nid: ${event.lines.map((line) => line.logged_at).reduce((a, b) => (a > b ? a : b))}\ndata: ${JSON.stringify(event.lines)}\n\n`;
		case 'done':
			return 'event: done\ndata: {}\n\n';
	}
}
