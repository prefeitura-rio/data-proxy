import { createHash, randomBytes } from 'node:crypto';
import * as v from 'valibot';
import type { Session } from './session';

export type OidcConfig = {
	issuer: string;
	clientId: string;
	clientSecret: string;
	redirectUri: string;
};

const Discovery = v.object({
	authorization_endpoint: v.string(),
	token_endpoint: v.string(),
	end_session_endpoint: v.optional(v.string())
});

const TokenResponse = v.object({
	access_token: v.string(),
	refresh_token: v.optional(v.string()),
	expires_in: v.number()
});

export type Discovery = v.InferOutput<typeof Discovery>;
type TokenResponse = v.InferOutput<typeof TokenResponse>;

export async function discover(issuer: string, fetchFn: typeof fetch = fetch): Promise<Discovery> {
	const response = await fetchFn(`${issuer.replace(/\/$/, '')}/.well-known/openid-configuration`);

	if (!response.ok) {
		throw new Error(`The identity provider answered ${response.status} to the discovery request`);
	}

	return v.parse(Discovery, await response.json());
}

export function pkce(): { verifier: string; challenge: string } {
	const verifier = randomBytes(32).toString('base64url');

	return { verifier, challenge: createHash('sha256').update(verifier).digest('base64url') };
}

export const randomState = () => randomBytes(16).toString('base64url');

export function authorizeUrl(
	discovery: Discovery,
	config: OidcConfig,
	{ state, challenge }: { state: string; challenge: string }
): URL {
	const url = new URL(discovery.authorization_endpoint);

	url.search = new URLSearchParams({
		response_type: 'code',
		client_id: config.clientId,
		redirect_uri: config.redirectUri,
		scope: 'openid',
		state,
		code_challenge: challenge,
		code_challenge_method: 'S256'
	}).toString();

	return url;
}

async function token(
	discovery: Discovery,
	body: Record<string, string>,
	fetchFn: typeof fetch
): Promise<TokenResponse> {
	const response = await fetchFn(discovery.token_endpoint, {
		method: 'POST',
		headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
		body: new URLSearchParams(body).toString()
	});

	if (!response.ok) {
		throw new Error(`The identity provider answered ${response.status} to the token request`);
	}

	return v.parse(TokenResponse, await response.json());
}

export async function exchangeCode(
	discovery: Discovery,
	config: OidcConfig,
	code: string,
	verifier: string,
	fetchFn: typeof fetch = fetch,
	now = Date.now()
): Promise<Session> {
	const tokens = await token(
		discovery,
		{
			grant_type: 'authorization_code',
			code,
			code_verifier: verifier,
			redirect_uri: config.redirectUri,
			client_id: config.clientId,
			client_secret: config.clientSecret
		},
		fetchFn
	);

	return {
		accessToken: tokens.access_token,
		refreshToken: tokens.refresh_token ?? '',
		expiresAt: now + tokens.expires_in * 1000
	};
}

export async function refreshSession(
	discovery: Discovery,
	config: OidcConfig,
	refreshToken: string,
	fetchFn: typeof fetch = fetch,
	now = Date.now()
): Promise<Session> {
	const tokens = await token(
		discovery,
		{
			grant_type: 'refresh_token',
			refresh_token: refreshToken,
			client_id: config.clientId,
			client_secret: config.clientSecret
		},
		fetchFn
	);

	return {
		accessToken: tokens.access_token,
		refreshToken: tokens.refresh_token ?? refreshToken,
		expiresAt: now + tokens.expires_in * 1000
	};
}
