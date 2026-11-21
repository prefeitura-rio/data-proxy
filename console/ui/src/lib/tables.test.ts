import { describe, expect, it } from 'vitest';
import { destination } from './tables';

describe('destination', () => {
	it.each([
		{ table: 'project.dataset.orders', name: 'orders' },
		{ table: 'orders', name: 'orders' },
		{ table: 'a.b.c.d', name: 'd' },
		{ table: 'dataset.', name: '' }
	])('gives $name for $table', ({ table, name }) => {
		expect(destination(table)).toBe(name);
	});
});
