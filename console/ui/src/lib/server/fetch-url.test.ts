import { describe, expect, it } from 'vitest';
import { requestUrl } from './fetch-url';

describe('requestUrl', () => {
	it.each([
		{ name: 'a text', input: 'https://api.test/a?b=1', url: 'https://api.test/a?b=1' },
		{ name: 'a URL', input: new URL('https://api.test/a'), url: 'https://api.test/a' },
		{ name: 'a request', input: new Request('https://api.test/a'), url: 'https://api.test/a' }
	])('reads the address of $name', ({ input, url }) => {
		expect(requestUrl(input)).toBe(url);
	});
});
