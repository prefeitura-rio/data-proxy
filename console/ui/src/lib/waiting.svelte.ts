import { invalidate } from '$app/navigation';
import type { SubmissionOptions } from './enhance.svelte.js';

const EVERY_MS = 1000;
const GIVE_UP_MS = 20_000;

/**
 * Wait for the worker to change something that a form asked for. The worker acts a moment after the page shows its
 * answer, so after an action the page reloads its data every second until `read` gives a new value, or 20 seconds pass.
 * Pass `options` to the `Submission` of each form that asks for the change. Call it while a component starts.
 */
export function waitForChange(key: string, read: () => string) {
	let from = $state<string | null>(null);
	let before = '';

	$effect(() => {
		if (from === null) {
			return;
		}

		if (read() !== from) {
			from = null;
			return;
		}

		const timer = setInterval(() => void invalidate(key), EVERY_MS);
		const stop = setTimeout(() => {
			from = null;
		}, GIVE_UP_MS);

		return () => {
			clearInterval(timer);
			clearTimeout(stop);
		};
	});

	const options: SubmissionOptions = {
		begin: () => {
			before = read();

			return () => undefined;
		},
		onupdated: () => {
			from = before;
		}
	};

	return {
		get waiting() {
			return from !== null;
		},
		options
	};
}
