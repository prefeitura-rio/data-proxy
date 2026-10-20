import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { urlOf } from './fetch-url';
import { authorizeUrl, discover, exchangeCode, pkce, refreshSession, type Discovery, type OidcConfig } from './oidc';

const discovery: Discovery = {
	authorization_endpoint: 'http://idp/auth',
	token_endpoint: 'http://idp/token',
	end_session_endpoint: 'http://idp/logout'
};
const config: OidcConfig = {
	issuer: 'http://idp',
	clientId: 'console',
	clientSecret: 'shh',
	redirectUri: 'http://console/auth/callback'
};

function tokenFetch(body: unknown, status = 200) {
	const calls: { url: string; body: URLSearchParams }[] = [];
	const fetchFn: typeof fetch = (url, init) => {
		calls.push({ url: urlOf(url), body: new URLSearchParams(typeof init?.body === 'string' ? init.body : '') });

		return Promise.resolve(new Response(JSON.stringify(body), { status }));
	};
	return { calls, fetchFn };
}

describe('pkce', () => {
	it('derives the challenge from the verifier with SHA-256', () => {
		const { verifier, challenge } = pkce();

		expect(challenge).toBe(createHash('sha256').update(verifier).digest('base64url'));
		expect(verifier.length).toBeGreaterThanOrEqual(43);
	});

	it('makes a new verifier each time', () => {
		expect(pkce().verifier).not.toBe(pkce().verifier);
	});
});

describe('authorizeUrl', () => {
	it('asks for the code flow with PKCE and the state', () => {
		const url = authorizeUrl(discovery, config, { state: 's1', challenge: 'c1' });

		expect(url.origin + url.pathname).toBe('http://idp/auth');
		expect(Object.fromEntries(url.searchParams)).toEqual({
			response_type: 'code',
			client_id: 'console',
			redirect_uri: 'http://console/auth/callback',
			scope: 'openid',
			state: 's1',
			code_challenge: 'c1',
			code_challenge_method: 'S256'
		});
	});
});

describe('exchangeCode', () => {
	it('sends the code, the verifier and the client secret, and returns a session', async () => {
		const { calls, fetchFn } = tokenFetch({ access_token: 'a', refresh_token: 'r', expires_in: 300 });

		const session = await exchangeCode(discovery, config, 'code1', 'verifier1', fetchFn, 1_000);

		expect(session).toEqual({ accessToken: 'a', refreshToken: 'r', expiresAt: 1_000 + 300_000 });
		expect(calls[0]?.url).toBe('http://idp/token');
		expect(Object.fromEntries(calls[0]?.body ?? [])).toEqual({
			grant_type: 'authorization_code',
			code: 'code1',
			code_verifier: 'verifier1',
			redirect_uri: 'http://console/auth/callback',
			client_id: 'console',
			client_secret: 'shh'
		});
	});

	it('fails when the provider refuses the code', async () => {
		const { fetchFn } = tokenFetch({ error: 'invalid_grant' }, 400);

		await expect(exchangeCode(discovery, config, 'bad', 'v', fetchFn, 0)).rejects.toThrow('400');
	});
});

describe('refreshSession', () => {
	it('exchanges the refresh token for a new session', async () => {
		const { calls, fetchFn } = tokenFetch({ access_token: 'a2', refresh_token: 'r2', expires_in: 300 });

		const session = await refreshSession(discovery, config, 'r1', fetchFn, 5_000);

		expect(session).toEqual({ accessToken: 'a2', refreshToken: 'r2', expiresAt: 305_000 });
		expect(Object.fromEntries(calls[0]?.body ?? [])).toMatchObject({
			grant_type: 'refresh_token',
			refresh_token: 'r1'
		});
	});

	it('keeps the old refresh token when the provider sends no new one', async () => {
		const { fetchFn } = tokenFetch({ access_token: 'a2', expires_in: 300 });

		expect((await refreshSession(discovery, config, 'r1', fetchFn, 0)).refreshToken).toBe('r1');
	});

	it('fails when the refresh token has expired', async () => {
		const { fetchFn } = tokenFetch({ error: 'invalid_grant' }, 400);

		await expect(refreshSession(discovery, config, 'old', fetchFn, 0)).rejects.toThrow('400');
	});
});

describe('answers of the identity provider', () => {
	it.each([
		{ name: 'a discovery without the token endpoint', body: { authorization_endpoint: 'http://idp/auth' } },
		{ name: 'a discovery that is not an object', body: 'text' }
	])('refuses $name', async ({ body }) => {
		const { fetchFn } = tokenFetch(body);

		await expect(discover('http://idp', fetchFn)).rejects.toThrow();
	});

	it.each([
		{ name: 'a token answer without an access token', body: { expires_in: 300 } },
		{ name: 'a token answer with a text for the lifetime', body: { access_token: 'a', expires_in: 'soon' } }
	])('refuses $name', async ({ body }) => {
		const { fetchFn } = tokenFetch(body);

		await expect(exchangeCode(discovery, config, 'c', 'v', fetchFn)).rejects.toThrow();
	});
});
