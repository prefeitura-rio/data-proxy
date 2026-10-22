import * as v from 'valibot';
import { LogLine } from './types';

export type Follower = {
	/** Fresh log lines. */
	onLines: (lines: LogLine[]) => void;
	onStatus: (status: string) => void;
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

	const source = new EventSource(`/sync/runs/${runId}/logs?${query}`);

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
	source.onerror = () => {
		if (source.readyState === EventSource.CLOSED) {
			follower.onEnd();
		}
	};

	return () => {
		source.close();
	};
}
