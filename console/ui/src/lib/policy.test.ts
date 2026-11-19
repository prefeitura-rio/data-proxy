import { describe, expect, it } from 'vitest';
import { MAX_UNITS, parseUnits } from './policy';

const rows = (unitType: string, ...ids: string[]) => ({
	ok: true,
	rows: ids.map((unit_id) => ({ unit_type: unitType, unit_id }))
});
const refused = (message: string) => ({ ok: false, message });

describe('parseUnits', () => {
	it.each([
		['one id', 'area', 'SMAS', rows('area', 'SMAS')],
		['a list with no space', 'area', 'SMAS,SMTR', rows('area', 'SMAS', 'SMTR')],
		['a list with a space', 'area', 'SMAS, SMTR', rows('area', 'SMAS', 'SMTR')],
		['a list with spaces on both sides', 'area', ' SMAS ,  SMTR ', rows('area', 'SMAS', 'SMTR')],
		['a list with a mix of both formats', 'area', 'SMAS,SMTR, SMS', rows('area', 'SMAS', 'SMTR', 'SMS')],
		['a list on new lines', 'area', 'SMAS\nSMTR', rows('area', 'SMAS', 'SMTR')],
		['a list on Windows new lines', 'area', 'SMAS\r\nSMTR', rows('area', 'SMAS', 'SMTR')],
		['a list with commas and new lines', 'area', 'SMAS, SMTR\nSMS', rows('area', 'SMAS', 'SMTR', 'SMS')],
		['empty items', 'area', 'SMAS,, SMTR,', rows('area', 'SMAS', 'SMTR')],
		['a repeated id', 'area', 'SMAS, SMTR, SMAS', rows('area', 'SMAS', 'SMTR')],
		['an id with a space inside', 'area', 'Sao Paulo, SMAS', rows('area', 'Sao Paulo', 'SMAS')],
		['the wildcard grant', '*', '*', rows('*', '*')],
		['the wildcard with spaces', '*', ' * ', rows('*', '*')]
	])('accepts %s', (_name, unitType, text, expected) => {
		expect(parseUnits(unitType, text)).toEqual(expected);
	});

	it.each([
		['an empty type', '', 'SMAS', refused('Enter a unit type and at least one unit id.')],
		['an empty id', 'area', '', refused('Enter a unit type and at least one unit id.')],
		['only separators', 'area', ' , \n ,', refused('Enter a unit type and at least one unit id.')],
		['the wildcard with other ids', 'area', 'SMAS, *', refused('Use * alone, not with other ids.')],
		['the wildcard first in a list', '*', '*, SMAS', refused('Use * alone, not with other ids.')],
		['the wildcard id with a real type', 'area', '*', refused('The id * needs the unit type *.')],
		['the wildcard type with a real id', '*', 'SMAS', refused('The unit type * needs the id *.')]
	])('refuses %s', (_name, unitType, text, expected) => {
		expect(parseUnits(unitType, text)).toEqual(expected);
	});

	it('accepts exactly the limit and refuses one more', () => {
		const ids = (count: number) => Array.from({ length: count }, (_, index) => `a${index}`).join(', ');

		expect(parseUnits('area', ids(MAX_UNITS))).toMatchObject({ ok: true });
		expect(parseUnits('area', ids(MAX_UNITS + 1))).toEqual(refused(`At most ${MAX_UNITS} ids at once.`));
	});

	it('counts the ids after it drops the repeated ones', () => {
		const repeated = Array.from({ length: MAX_UNITS + 5 }, () => 'SMAS').join(',');

		expect(parseUnits('area', repeated)).toEqual(rows('area', 'SMAS'));
	});
});
