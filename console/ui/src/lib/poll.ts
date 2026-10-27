/** The refresh intervals that the reader can pick, in seconds. Zero means off. */
export const INTERVALS = [0, 2, 5, 10, 30, 60];

export const DEFAULT_INTERVAL = 5;

/** The key of the browser storage that keeps the interval. */
export const INTERVAL_KEY = 'refresh-interval';

/** Read the saved interval. A value that is not one of the choices means the default. */
export function readInterval(stored: string | null): number {
	const seconds = Number(stored);

	return stored !== null && INTERVALS.includes(seconds) ? seconds : DEFAULT_INTERVAL;
}

/** The wait before the next refresh in milliseconds, or null when the page must not refresh now. */
export function pollDelay({ seconds, hidden }: { seconds: number; hidden: boolean }): number | null {
	return seconds === 0 || hidden ? null : seconds * 1000;
}
