import { describe, expect, it } from 'vitest';
import { securityHeaders, settingsSources, withSources, type Sources } from './headers';

type Merge = { name: string; policy: string; extra: Sources; expected: string };
type Case = { name: string; headers: Record<string, string>; header: string; expected: string };

describe('withSources', () => {
	const merges: Merge[] = [
		{
			name: 'adds a source to a directive that exists',
			policy: "default-src 'self'; frame-src 'self'",
			extra: { 'frame-src': ['https://notebooks.example'] },
			expected: "default-src 'self'; frame-src 'self' https://notebooks.example"
		},
		{
			name: 'adds a directive that the policy lacks',
			policy: "default-src 'self'",
			extra: { 'form-action': ["'self'", 'https://idp.example'] },
			expected: "default-src 'self'; form-action 'self' https://idp.example"
		},
		{
			name: 'changes two directives and keeps the others',
			policy: "default-src 'self'; frame-src 'self'; form-action 'self'",
			extra: { 'frame-src': ['https://a.example'], 'form-action': ['https://b.example'] },
			expected: "default-src 'self'; frame-src 'self' https://a.example; form-action 'self' https://b.example"
		},
		{
			name: 'keeps a policy when there is nothing to add',
			policy: "default-src 'self'",
			extra: {},
			expected: "default-src 'self'"
		}
	];

	it.each(merges)('$name', ({ policy, extra, expected }) => {
		expect(withSources(policy, extra)).toBe(expected);
	});
});

describe('securityHeaders', () => {
	const extra = { 'frame-src': ['https://notebooks.example'] };
	const cases: Case[] = [
		{
			name: 'adds the sources to the policy of the page',
			headers: { 'content-security-policy': "default-src 'self'; frame-src 'self'" },
			header: 'content-security-policy',
			expected: "default-src 'self'; frame-src 'self' https://notebooks.example"
		},
		{
			name: 'stops the browser from guessing a type',
			headers: {},
			header: 'x-content-type-options',
			expected: 'nosniff'
		},
		{
			name: 'sends the referrer to the same origin only',
			headers: {},
			header: 'referrer-policy',
			expected: 'same-origin'
		},
		{ name: 'stops the browser from storing a page', headers: {}, header: 'cache-control', expected: 'no-store' },
		{
			name: 'keeps the cache control that a route sets',
			headers: { 'cache-control': 'no-cache' },
			header: 'cache-control',
			expected: 'no-cache'
		}
	];

	it.each(cases)('$name', ({ headers, header, expected }) => {
		const found = new Headers(headers);

		securityHeaders(found, extra);

		expect(found.get(header)).toBe(expected);
	});

	it('writes no policy for a response that has none', () => {
		const found = new Headers();

		securityHeaders(found, extra);

		expect(found.has('content-security-policy')).toBe(false);
	});
});

describe('settingsSources', () => {
	it.each([
		{
			name: 'lets the page frame the notebook and send the sign-out to the identity provider',
			notebook: 'https://notebooks.example',
			expected: { 'form-action': ['https://idp.example'], 'frame-src': ['https://notebooks.example'] }
		},
		{
			name: 'adds no frame source when the console has no notebook',
			notebook: null,
			expected: { 'form-action': ['https://idp.example'] }
		}
	])('$name', ({ notebook, expected }) => {
		expect(settingsSources(notebook, 'https://idp.example/realms/dev')).toEqual(expected);
	});
});
