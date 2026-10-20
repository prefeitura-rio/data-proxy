import { describe, expect, it } from 'vitest';
import { can, tabsFor } from './permissions';

describe('tabsFor', () => {
	it.each([
		{ role: 'dp_api', permissions: ['read_api'], tabs: [] },
		{ role: 'dp_policy', permissions: ['read_policy', 'create_policy'], tabs: ['access'] },
		{ role: 'dp_console', permissions: ['read_api', 'read_sync', 'read_config'], tabs: ['sync', 'config'] },
		{
			role: 'dp_admin',
			permissions: ['read_sync', 'read_config', 'read_policy', 'read_files', 'start_sync'],
			tabs: ['sync', 'config', 'access', 'query']
		}
	])('shows $tabs to $role', ({ permissions, tabs }) => {
		expect(tabsFor(permissions).map((tab) => tab.id)).toEqual(tabs);
	});

	it('links each tab to its nested route', () => {
		expect(tabsFor(['read_sync']).map((tab) => tab.href)).toEqual(['/sync']);
	});
});

describe('can', () => {
	it('is true only for a permission in the list', () => {
		expect(can(['read_sync', 'start_sync'], 'start_sync')).toBe(true);
		expect(can(['read_sync'], 'start_sync')).toBe(false);
	});
});
