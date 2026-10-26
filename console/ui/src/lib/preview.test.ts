import { describe, expect, it } from 'vitest';
import { actionTone, summaryText } from './preview';

const rows = (...actions: string[]) => actions.map((action, index) => ({ action, key: `row-${index}` }));

describe('summaryText', () => {
	it.each([
		['an import that changes nothing', rows('unchanged', 'unchanged'), 'Nothing changes: 2 rows are the same.'],
		['one insert', rows('insert'), '1 insert, 0 updates, 0 deletes, 0 the same.'],
		['a mix', rows('insert', 'insert', 'update', 'delete', 'unchanged'), '2 inserts, 1 update, 1 delete, 1 the same.'],
		['no rows', [], 'The document holds nothing to import.']
	])('describes %s', (_, changes, text) => {
		expect(summaryText(changes)).toBe(text);
	});

	it('counts an unknown action as nothing', () => {
		expect(summaryText(rows('insert', 'surprise'))).toBe('1 insert, 0 updates, 0 deletes, 0 the same.');
	});
});

describe('actionTone', () => {
	it.each([
		['an insert', 'insert', 'badge-success'],
		['an update', 'update', 'badge-warning'],
		['a delete', 'delete', 'badge-error'],
		['a row that stays', 'unchanged', 'badge-neutral']
	])('colors %s', (_, action, tone) => {
		expect(actionTone(action)).toBe(tone);
	});
});
