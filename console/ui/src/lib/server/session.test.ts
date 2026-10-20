import { describe, expect, it } from 'vitest';
import { REFRESH_BEFORE_MS, chunk, isExpired, needsRefresh, seal, unchunk, unseal, type Session } from './session';

const secret = 'a secret of the test';
const session: Session = { accessToken: 'access', refreshToken: 'refresh', expiresAt: 1_000_000 };

describe('seal and unseal', () => {
	it('returns the session that it sealed', () => {
		expect(unseal(seal(session, secret), secret)).toEqual(session);
	});

	it('refuses a value that another secret sealed', () => {
		expect(unseal(seal(session, secret), 'another secret')).toBeNull();
	});

	it('refuses a value that someone changed', () => {
		const sealed = seal(session, secret);
		const changed = sealed.slice(0, -2) + (sealed.endsWith('AA') ? 'BB' : 'AA');

		expect(unseal(changed, secret)).toBeNull();
	});

	it('refuses a value that is not a session', () => {
		expect(unseal('not a sealed value', secret)).toBeNull();
	});

	it('seals the same session to a different value each time', () => {
		expect(seal(session, secret)).not.toEqual(seal(session, secret));
	});
});

describe('expiry', () => {
	it.each([
		{ name: 'long before the expiry', now: 0, refresh: false, expired: false },
		{ name: 'at the start of the refresh window', now: 1_000_000 - REFRESH_BEFORE_MS, refresh: true, expired: false },
		{ name: 'just before the expiry', now: 999_999, refresh: true, expired: false },
		{ name: 'at the expiry', now: 1_000_000, refresh: true, expired: true }
	])('$name', ({ now, refresh, expired }) => {
		expect(needsRefresh(session, now)).toBe(refresh);
		expect(isExpired(session, now)).toBe(expired);
	});

	it('refreshes 60 seconds before the expiry', () => {
		expect(REFRESH_BEFORE_MS).toBe(60_000);
	});
});

describe('chunk', () => {
	it('splits a value into pieces that fit in a cookie and joins them again', () => {
		const value = 'x'.repeat(9000);
		const pieces = chunk(value, 3500);

		expect(pieces.map((piece) => piece.length)).toEqual([3500, 3500, 2000]);
		expect(unchunk(pieces)).toBe(value);
	});

	it('keeps a short value in one piece', () => {
		expect(chunk('short', 3500)).toEqual(['short']);
	});
});
