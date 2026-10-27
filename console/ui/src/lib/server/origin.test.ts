import { describe, expect, it } from 'vitest';
import { isSecure, sameOrigin } from './origin';

describe('sameOrigin', () => {
	it.each([
		{ name: 'a read needs no origin', method: 'GET', origin: null, expected: true },
		{ name: 'a write from the same origin', method: 'POST', origin: 'http://console.localhost', expected: true },
		{ name: 'a write from another origin', method: 'POST', origin: 'http://evil.example', expected: false },
		{ name: 'a write without an origin', method: 'POST', origin: null, expected: false },
		{ name: 'a delete from another origin', method: 'DELETE', origin: 'http://evil.example', expected: false }
	])('$name', ({ method, origin, expected }) => {
		expect(sameOrigin(method, origin, 'http://console.localhost')).toBe(expected);
	});
});

describe('isSecure', () => {
	it.each([
		{ name: 'an https origin', origin: 'https://console.example', expected: true },
		{ name: 'an http origin', origin: 'http://console.localhost', expected: false }
	])('$name', ({ origin, expected }) => {
		expect(isSecure(origin)).toBe(expected);
	});
});
