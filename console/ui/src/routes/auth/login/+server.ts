import { resolve } from '$app/paths';
import { redirect } from '@sveltejs/kit';
import { BASE } from '#lib/base.js';
import { oidcConfig, provider } from '#lib/server/auth.js';
import { authorizeUrl, pkce, randomState } from '#lib/server/oidc.js';
import { isSecure } from '#lib/server/origin.js';
import { sealText } from '#lib/server/session.js';
import { settings } from '#lib/server/settings.js';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ url, cookies }) => {
	const { verifier, challenge } = pkce();
	const state = randomState();
	const next = url.searchParams.get('next') ?? resolve('/sync');

	cookies.set('oidc', sealText(JSON.stringify({ state, verifier, next }), settings().cookieSecret), {
		path: `${BASE}/auth`,
		httpOnly: true,
		sameSite: 'lax',
		secure: isSecure(settings().origin),
		maxAge: 600
	});

	const discovery = await provider();

	redirect(302, authorizeUrl(discovery, oidcConfig(), { state, challenge }).toString(), {
		external: [new URL(discovery.authorization_endpoint).origin]
	});
};
