import { describe, expect, it } from 'vitest';
import { isCron } from './cron';

describe('isCron', () => {
	it.each([
		{ name: 'five fields', text: '0 2 * * *', expected: true },
		{ name: 'six fields, with the seconds first', text: '*/30 0 2 * * *', expected: true },
		{ name: 'a list, a range and a step', text: '0,30 1-5 */2 * *', expected: true },
		{ name: 'a day name', text: '0 2 * * mon', expected: true },
		{ name: 'four fields', text: '0 2 * *', expected: false },
		{ name: 'seven fields', text: '0 2 * * * * *', expected: false },
		{ name: 'two spaces between fields', text: '0  2 * * *', expected: false },
		{ name: 'a space at the end', text: '0 2 * * * ', expected: false },
		{ name: 'a character that is not part of a cron', text: '0 2 * * ;', expected: false },
		{ name: 'an empty text', text: '', expected: false }
	])('says $expected for $name', ({ text, expected }) => {
		expect(isCron(text)).toBe(expected);
	});
});
