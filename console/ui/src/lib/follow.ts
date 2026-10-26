import * as v from 'valibot';
import { resolve } from '$app/paths';
import { LogLine } from './types';

export type Follower = {
	/** Fresh log lines. */
	onLines: (lines: LogLine[]) => void;
	onStatus: (status: string) => void;
	/** The connection opened, dropped and is trying again, or is over. */
	onState?: (state: 'live' | 'reconnecting') => void;
	/** The stream is over: the run finished, or the server refused the stream. */
	onEnd: () => void;
};

const Status = v.object({ status: v.string() });

/**
 * Open the stream of a run and call the follower for each event. The browser reconnects by itself after a dropped
 * connection and sends the time of the last line. It returns the function that closes the stream.
 */
export function followRun(runId: string, options: { after: string | null; settle: boolean }, follower: Follower) {
	const query = new URLSearchParams();

	if (options.after) {
		query.set('after', options.after);
	}

	if (options.settle) {
		query.set('settle', '1');
	}

	const source = new EventSource(`${resolve('/sync/runs/[id]/logs', { id: runId })}?${query}`);

	source.addEventListener('lines', (event: MessageEvent<string>) => {
		follower.onLines(v.parse(v.array(LogLine), JSON.parse(event.data)));
	});
	source.addEventListener('status', (event: MessageEvent<string>) => {
		follower.onStatus(v.parse(Status, JSON.parse(event.data)).status);
	});
	source.addEventListener('done', () => {
		source.close();
		follower.onEnd();
	});
	source.onopen = () => {
		follower.onState?.('live');
	};
	source.onerror = () => {
		if (source.readyState === EventSource.CLOSED) {
			follower.onEnd();
		} else {
			follower.onState?.('reconnecting');
		}
	};

	return () => {
		source.close();
	};
}
