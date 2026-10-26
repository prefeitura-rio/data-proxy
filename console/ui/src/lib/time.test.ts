import { describe, expect, it } from 'vitest';
import { duration, shortTime } from './time';

const NOW = Date.parse('2026-10-25T12:00:00Z');

describe('shortTime', () => {
	it.each([
		['seconds ago', '2026-10-25T11:59:30Z', '30 seconds ago'],
		['minutes ago', '2026-10-25T11:55:00Z', '5 minutes ago'],
		['hours ago', '2026-10-25T09:00:00Z', '3 hours ago'],
		['a future time', '2026-10-25T12:10:00Z', 'in 10 minutes']
	])('reads %s as a relative time', (_name, iso, expected) => {
		expect(shortTime(iso, NOW, 'en')).toBe(expected);
	});

	it('reads a time older than one day as a date', () => {
		expect(shortTime('2026-10-01T12:00:00Z', NOW, 'en')).toMatch(/Oct 1, 2026/);
	});

	it('returns a text that is not a time as it is', () => {
		expect(shortTime('not a time', NOW, 'en')).toBe('not a time');
	});
});

describe('duration', () => {
	it.each([
		['seconds', 42_000, '42 s'],
		['minutes', 65_000, '1 min 5 s'],
		['hours', 3_900_000, '1 h 5 min'],
		['a negative span', -5000, '0 s']
	])('writes %s', (_name, milliseconds, expected) => {
		expect(duration(milliseconds)).toBe(expected);
	});
});
