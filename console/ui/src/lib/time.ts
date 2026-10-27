const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
	['second', 60],
	['minute', 60],
	['hour', 24]
];

/** A time of the last day reads as "5 minutes ago". An older time reads as a date, in the zone of the reader. */
export function shortTime(iso: string, now: number, locale?: string): string {
	const time = Date.parse(iso);

	if (Number.isNaN(time)) {
		return iso;
	}

	let seconds = Math.round((time - now) / 1000);

	if (Math.abs(seconds) < 86_400) {
		const formatter = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });

		for (const [unit, size] of UNITS) {
			if (Math.abs(seconds) < size) {
				return formatter.format(seconds, unit);
			}

			seconds = Math.round(seconds / size);
		}

		return formatter.format(seconds, 'day');
	}

	return new Date(time).toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' });
}

/** The full time, in the zone of the reader, for the tooltip. */
export function fullTime(iso: string, locale?: string): string {
	const time = Date.parse(iso);

	return Number.isNaN(time) ? iso : new Date(time).toLocaleString(locale, { dateStyle: 'full', timeStyle: 'long' });
}

/** How long a run lasted: "1 min 5 s". */
export function duration(milliseconds: number): string {
	const total = Math.max(0, Math.round(milliseconds / 1000));
	const hours = Math.floor(total / 3600);
	const minutes = Math.floor((total % 3600) / 60);
	const seconds = total % 60;

	if (hours > 0) {
		return `${hours} h ${minutes} min`;
	}

	return minutes > 0 ? `${minutes} min ${seconds} s` : `${seconds} s`;
}
