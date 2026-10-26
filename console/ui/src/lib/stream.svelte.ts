import { untrack } from 'svelte';
import { invalidate } from '$app/navigation';
import { followRun } from './follow.js';
import { lastLoggedAt, mergeLogs } from './logs.js';
import { isActive } from './status.js';
import type { LogLine } from './types.js';

/** What the stream starts from: the id of the run, and the logs and the status that the page loaded. */
export type RunSource = { id: () => string; logs: () => LogLine[]; status: () => string };

/**
 * Follow a run while `live` is true. The stream adds log lines and a newer status on top of the loaded data, and it
 * asks the page to reload the tasks and the steps when the status changes. Call it while a component starts.
 */
export function runStream(source: RunSource) {
	let streamed = $state<{ logs: LogLine[]; status: string } | null>(null);
	let live = $state(isActive(untrack(source.status)));
	let connection = $state<'live' | 'reconnecting'>('live');
	let settling = false;

	const logs = $derived(streamed?.logs ?? source.logs());
	const status = $derived(streamed?.status ?? source.status());

	$effect(() => {
		if (!live) {
			return;
		}

		const settle = untrack(() => settling);

		return followRun(
			source.id(),
			{ after: untrack(() => lastLoggedAt(logs)), settle },
			{
				onLines: (fresh) => {
					streamed = {
						logs: mergeLogs(
							untrack(() => logs),
							fresh
						),
						status: untrack(() => status)
					};
				},
				onStatus: (next) => {
					streamed = { logs: untrack(() => logs), status: next };
					void invalidate('app:run');
				},
				onState: (state) => {
					connection = state;
				},
				onEnd: () => {
					live = false;
					settling = false;
				}
			}
		);
	});

	return {
		get logs() {
			return logs;
		},
		get status() {
			return status;
		},
		get live() {
			return live;
		},
		set live(value: boolean) {
			live = value;
		},
		get connection() {
			return connection;
		},
		/** After a control action, follow the run even while it still shows a finished status. */
		watch() {
			settling = true;
			live = true;
		}
	};
}
