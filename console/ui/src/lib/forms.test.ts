import { describe, expect, it } from 'vitest';
import { list, optional, text, whole } from './forms';

const form = (entries: Record<string, string>) => {
	const data = new FormData();

	for (const [name, value] of Object.entries(entries)) {
		data.set(name, value);
	}

	return data;
};

describe('text and optional', () => {
	it('trims a value and turns a blank value into null when it is optional', () => {
		expect(text(form({ name: '  a ' }), 'name')).toBe('a');
		expect(text(form({}), 'name')).toBe('');
		expect(optional(form({ claim: '   ' }), 'claim')).toBeNull();
		expect(optional(form({ claim: 'x' }), 'claim')).toBe('x');
	});
});

describe('whole', () => {
	it.each([
		['a number', '12', 12],
		['zero', '0', 0],
		['a blank value', '  ', null],
		['a missing value', undefined, null]
	])('reads %s', (_name, value, expected) => {
		expect(whole(form(value === undefined ? {} : { n: value }), 'n')).toBe(expected);
	});

	it('keeps a value that is not a whole number, so the database refuses it', () => {
		expect(whole(form({ n: 'abc' }), 'n')).toBe('abc');
		expect(whole(form({ n: '1.5' }), 'n')).toBe('1.5');
	});
});

describe('list', () => {
	it('splits on commas, drops blanks, and returns null for an empty list', () => {
		expect(list(form({ sort: ' a, b ,,c ' }), 'sort')).toEqual(['a', 'b', 'c']);
		expect(list(form({ sort: ' , ' }), 'sort')).toBeNull();
		expect(list(form({}), 'sort')).toBeNull();
	});
});
