import { describe, expect, it } from 'vitest';
import { pollDelay, readInterval } from './poll';

describe('readInterval', () => {
	it.each([
		['a saved choice', '30', 30],
		['off', '0', 0],
		['nothing saved', null, 5],
		['a value that is not a choice', '7', 5],
		['a text', 'often', 5]
	])('reads %s', (_name, stored, expected) => {
		expect(readInterval(stored)).toBe(expected);
	});
});

describe('pollDelay', () => {
	it.each([
		['a visible tab', { seconds: 5, hidden: false }, 5000],
		['a hidden tab', { seconds: 5, hidden: true }, null],
		['an interval that is off', { seconds: 0, hidden: false }, null]
	])('gives the delay for %s', (_name, input, expected) => {
		expect(pollDelay(input)).toBe(expected);
	});
});
