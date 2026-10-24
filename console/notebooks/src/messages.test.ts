import * as v from 'valibot';
import { describe, expect, it } from 'vitest';
import { GrantMessage } from './messages.ts';

const grant = {
	schema: 'sales',
	tables: [{ name: 'people', files: [{ data: 'https://s/a', delete: null }] }],
	expiresAt: 1
};

describe('GrantMessage', () => {
	it.each([
		['a grant', { type: 'grant', grant }, true],
		['another type', { type: 'other', grant }, false],
		['a grant without tables', { type: 'grant', grant: { schema: 'sales', expiresAt: 1 } }, false],
		[
			'a file without a delete field',
			{ type: 'grant', grant: { ...grant, tables: [{ name: 't', files: [{ data: 'a' }] }] } },
			false
		],
		['text', 'grant', false],
		['nothing', undefined, false]
	])('judges %s', (_name, data, valid) => {
		expect(v.safeParse(GrantMessage, data).success).toBe(valid);
	});
});
