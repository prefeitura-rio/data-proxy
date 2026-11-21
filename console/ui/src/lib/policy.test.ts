import { describe, expect, it } from 'vitest';
import { explainPath, MAX_UNITS, parseUnits, scopesOf } from './policy';

const rows = (unitType: string, ...ids: string[]) => ({
	ok: true,
	rows: ids.map((unit_id) => ({ unit_type: unitType, unit_id }))
});
const refused = (message: string) => ({ ok: false, message });

describe('parseUnits', () => {
	it.each([
		['one id', 'region', 'north', rows('region', 'north')],
		['a list with no space', 'region', 'north,south', rows('region', 'north', 'south')],
		['a list with a space', 'region', 'north, south', rows('region', 'north', 'south')],
		['a list with spaces on both sides', 'region', ' north ,  south ', rows('region', 'north', 'south')],
		['a list with a mix of both formats', 'region', 'north,south, east', rows('region', 'north', 'south', 'east')],
		['a list on new lines', 'region', 'north\nsouth', rows('region', 'north', 'south')],
		['a list on Windows new lines', 'region', 'north\r\nsouth', rows('region', 'north', 'south')],
		['a list with commas and new lines', 'region', 'north, south\neast', rows('region', 'north', 'south', 'east')],
		['empty items', 'region', 'north,, south,', rows('region', 'north', 'south')],
		['a repeated id', 'region', 'north, south, north', rows('region', 'north', 'south')],
		['an id with a space inside', 'region', 'Sao Paulo, north', rows('region', 'Sao Paulo', 'north')],
		['the wildcard grant', '*', '*', rows('*', '*')],
		['the wildcard with spaces', '*', ' * ', rows('*', '*')]
	])('accepts %s', (_name, unitType, text, expected) => {
		expect(parseUnits(unitType, text)).toEqual(expected);
	});

	it.each([
		['an empty type', '', 'north', refused('Enter a unit type and at least one unit id.')],
		['an empty id', 'region', '', refused('Enter a unit type and at least one unit id.')],
		['only separators', 'region', ' , \n ,', refused('Enter a unit type and at least one unit id.')],
		['the wildcard with other ids', 'region', 'north, *', refused('Use * alone, not with other ids.')],
		['the wildcard first in a list', '*', '*, north', refused('Use * alone, not with other ids.')],
		['the wildcard id with a real type', 'region', '*', refused('The id * needs the unit type *.')],
		['the wildcard type with a real id', '*', 'north', refused('The unit type * needs the id *.')]
	])('refuses %s', (_name, unitType, text, expected) => {
		expect(parseUnits(unitType, text)).toEqual(expected);
	});

	it('accepts exactly the limit and refuses one more', () => {
		const ids = (count: number) => Array.from({ length: count }, (_, index) => `a${index}`).join(', ');

		expect(parseUnits('region', ids(MAX_UNITS))).toMatchObject({ ok: true });
		expect(parseUnits('region', ids(MAX_UNITS + 1))).toEqual(refused(`At most ${MAX_UNITS} ids at once.`));
	});

	it('counts the ids after it drops the repeated ones', () => {
		const repeated = Array.from({ length: MAX_UNITS + 5 }, () => 'north').join(',');

		expect(parseUnits('region', repeated)).toEqual(rows('region', 'north'));
	});
});

describe('scopesOf', () => {
	it.each([
		['a table path', 'default.test.big_table', ['', 'default', 'default.test', 'default.test.big_table']],
		['a schema path', 'default.test', ['', 'default', 'default.test']],
		['a project path', 'default', ['', 'default']]
	])('%s', (_name, path, expected) => {
		expect(scopesOf(path)).toEqual(expected);
	});
});

describe('explainPath', () => {
	const entries = [
		{ table_name: null, path: 'default.test' },
		{ table_name: 'big_table', path: 'default.test.big_table' },
		{ table_name: 'odd.name', path: null }
	];

	it.each([
		['a table of the catalog', 'p.d.big_table', 'default.test.big_table'],
		['a table that ltree cannot store', 'p.d.odd.name', 'default.test'],
		['a table that the catalog lacks', 'p.d.missing', 'default.test'],
		['no table', null, 'default.test'],
		['an empty table', '', 'default.test']
	])('%s', (_name, table, expected) => {
		expect(explainPath(entries, table)).toBe(expected);
	});

	it('gives null for a schema that the catalog lacks', () => {
		expect(explainPath([], 'p.d.big_table')).toBeNull();
	});
});
