import { describe, expect, it } from 'vitest';
import { identityOf } from './identity';

const token = (claims: object) => `header.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.signature`;

describe('identityOf', () => {
	it.each([
		[
			'the user name and the role',
			{ preferred_username: 'web_admin', role: 'dp_admin', sub: 'abc' },
			'web_admin',
			'dp_admin'
		],
		['the subject when there is no user name', { role: 'dp_console', sub: 'abc' }, 'abc', 'dp_console'],
		['an empty role when the token has none', { preferred_username: 'web_viewer' }, 'web_viewer', '']
	])('reads %s', (_, claims, name, role) => {
		expect(identityOf(token(claims))).toEqual({ name, role });
	});

	it.each([
		['a token without a payload', 'header'],
		['a payload that is not JSON', 'a.bm90IGpzb24.c'],
		['a payload without a name or a subject', token({ role: 'dp_admin' })]
	])('returns null for %s', (_, value) => {
		expect(identityOf(value)).toBeNull();
	});
});
