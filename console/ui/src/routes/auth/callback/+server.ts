import { error, redirect } from '@sveltejs/kit';
import * as v from 'valibot';
import { oidcConfig, provider, writeSession } from '#lib/server/auth.js';
import { exchangeCode } from '#lib/server/oidc.js';
import { unsealText } from '#lib/server/session.js';
import { settings } from '#lib/server/settings.js';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ url, cookies }) => {
	const sealed = cookies.get('oidc');
	const text = sealed ? unsealText(sealed, settings().cookieSecret) : null;
	cookies.delete('oidc', { path: '/auth' });

	const code = url.searchParams.get('code');
	const parsed = v.safeParse(
		v.pipe(v.string(), v.parseJson(), v.object({ state: v.string(), verifier: v.string(), next: v.string() })),
		text
	);
	const login = parsed.success ? parsed.output : null;

	if (!code || !login) {
		error(400, 'The sign-in answer does not match the request');
	}

	if (login.state !== url.searchParams.get('state')) {
		error(400, 'The sign-in answer does not match the request');
	}

	const session = await exchangeCode(await provider(), oidcConfig(), code, login.verifier);
	writeSession(cookies, session, url.protocol === 'https:');

	redirect(303, login.next.startsWith('/') && !login.next.startsWith('//') ? login.next : '/sync');
};
