import { invalidate } from '$app/navigation';
import { INTERVAL_KEY, pollDelay, readInterval } from './poll.js';

/**
 * Refresh the data of a page at the interval that the reader picked. The page names its data with `depends(key)` in
 * its load function. The refresh pauses while the tab is hidden and runs immediately when the tab shows again. Call it
 * while a component starts.
 */
export function poll(key: string) {
	let seconds = $state(readInterval(null));
	let hidden = $state(false);
	let refreshing = $state(false);
	let updated = $state(Date.now());

	async function refresh() {
		refreshing = true;

		try {
			await invalidate(key);
		} finally {
			refreshing = false;
			updated = Date.now();
		}
	}

	$effect(() => {
		try {
			seconds = readInterval(localStorage.getItem(INTERVAL_KEY));
		} catch {
			seconds = readInterval(null);
		}

		const onVisibility = () => {
			hidden = document.hidden;

			if (!hidden) {
				void refresh();
			}
		};

		document.addEventListener('visibilitychange', onVisibility);

		return () => {
			document.removeEventListener('visibilitychange', onVisibility);
		};
	});

	$effect(() => {
		const delay = pollDelay({ seconds, hidden });

		if (delay === null) {
			return;
		}

		const timer = setInterval(() => void refresh(), delay);

		return () => {
			clearInterval(timer);
		};
	});

	return {
		get seconds() {
			return seconds;
		},
		set seconds(value: number) {
			seconds = value;

			try {
				localStorage.setItem(INTERVAL_KEY, String(value));
			} catch {
				return;
			}
		},
		get refreshing() {
			return refreshing;
		},
		get updated() {
			return updated;
		},
		refresh
	};
}
