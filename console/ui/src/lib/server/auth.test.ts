import type { Cookies } from '@sveltejs/kit';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { clearSession, freshSession, oidcConfig, writeSession } from './auth';
import { discover, refreshSession } from './oidc';
import { seal, unseal, type Session } from './session';

vi.mock('./oidc', () => ({ discover: vi.fn(), refreshSession: vi.fn() }));

vi.mock('./settings', () => ({
	settings: () => ({
		issuer: 'https://idp.example.test/realms/dev',
		clientId: 'data-proxy-console-web',
		clientSecret: 'secret',
		cookieSecret: 'cookie',
		origin: 'https://data-proxy.dados.rio'
	})
}));

describe('oidcConfig', () => {
	it('sends the identity provider back to the console path of the API host', () => {
		expect(oidcConfig().redirectUri).toBe('https://data-proxy.dados.rio/console/auth/callback');
	});
});

/** The cookies of one request: a map that has the calls of the code under test. */
function jar(initial: Record<string, string> = {}) {
	const store = new Map(Object.entries(initial));
	const cookies: Cookies = {
		getAll: () => [...store].map(([name, value]) => ({ name, value })),
		get: (name: string) => store.get(name),
		set: (name: string, value: string) => {
			store.set(name, value);
		},
		delete: (name: string) => {
			store.delete(name);
		},
		parse: (header: string) => ({ name: header, value: '' }),
		serialize: (name: string, value: string) => `${name}=${value}`
	};

	return { store, cookies };
}

const IDP = {
	authorization_endpoint: 'https://idp.example.test/auth',
	token_endpoint: 'https://idp.example.test/token'
};
const NOW = Date.now();
const session = (expiresInMs: number): Session => ({
	accessToken: 'access',
	refreshToken: 'refresh',
	expiresAt: NOW + expiresInMs
});
const stored = (value: Session) => ({ 'session.0': seal(value, 'cookie') });

describe('freshSession', () => {
	beforeEach(() => {
		vi.mocked(refreshSession).mockReset();
		vi.mocked(discover).mockReset();
	});

	it('returns nothing without a cookie of the session', async () => {
		expect(await freshSession(jar().cookies, false)).toBeNull();
	});

	it('returns the session that is fresh enough, with no call to the identity provider', async () => {
		const { cookies } = jar(stored(session(10 * 60_000)));

		expect(await freshSession(cookies, false)).toMatchObject({ accessToken: 'access' });
		expect(refreshSession).not.toHaveBeenCalled();
	});

	it('returns nothing for a cookie that it cannot open', async () => {
		expect(await freshSession(jar({ 'session.0': 'not a sealed value' }).cookies, false)).toBeNull();
	});

	it('refreshes a session that is about to end, and saves the new one', async () => {
		const fresh = { accessToken: 'new', refreshToken: 'new-refresh', expiresAt: NOW + 10 * 60_000 };
		vi.mocked(discover).mockResolvedValue(IDP);
		vi.mocked(refreshSession).mockResolvedValue(fresh);
		const { cookies, store } = jar(stored(session(30_000)));

		expect(await freshSession(cookies, false)).toEqual(fresh);
		expect(unseal(store.get('session.0') ?? '', 'cookie')).toEqual(fresh);
	});

	it('keeps a session that has time left when the refresh fails, and clears the cookies', async () => {
		vi.mocked(discover).mockResolvedValue(IDP);
		vi.mocked(refreshSession).mockRejectedValue(new Error('down'));
		const { cookies, store } = jar(stored(session(30_000)));

		expect(await freshSession(cookies, false)).toMatchObject({ accessToken: 'access' });
		expect(store.size).toBe(0);
	});

	it('sends the user to sign in again when the refresh fails after the session ended', async () => {
		vi.mocked(discover).mockResolvedValue(IDP);
		vi.mocked(refreshSession).mockRejectedValue(new Error('down'));

		expect(await freshSession(jar(stored(session(-1000))).cookies, false)).toBeNull();
	});
});

describe('the cookies of the session', () => {
	it('splits a long session in pieces, and reads them back in order', async () => {
		const long: Session = { accessToken: 'a'.repeat(9000), refreshToken: 'r', expiresAt: NOW + 10 * 60_000 };
		const { cookies, store } = jar();

		writeSession(cookies, long, false);

		expect([...store.keys()].sort()).toEqual(['session.0', 'session.1', 'session.2', 'session.3']);
		expect(await freshSession(cookies, false)).toEqual(long);
	});

	it('replaces the old pieces and clears only the pieces of the session', () => {
		const { cookies, store } = jar({ 'session.0': 'x', 'session.1': 'y', theme: 'dark' });

		writeSession(cookies, session(60_000), false);
		clearSession(cookies);

		expect([...store.keys()]).toEqual(['theme']);
	});
});

describe('provider', () => {
	it('asks the identity provider once, and asks again after a failure', async () => {
		vi.resetModules();
		const { provider: fresh } = await import('./auth');
		const { discover: ask } = await import('./oidc');
		vi.mocked(ask).mockRejectedValueOnce(new Error('down')).mockResolvedValue(IDP);

		await expect(fresh()).rejects.toThrow('down');
		await fresh();
		await fresh();

		expect(ask).toHaveBeenCalledTimes(2);
	});
});
