import { redirect } from '@sveltejs/kit';
import { clearSession, oidcConfig, provider } from '#lib/server/auth.js';
import { settings } from '#lib/server/settings.js';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ cookies }) => {
	clearSession(cookies);

	const { end_session_endpoint } = await provider();
	const { clientId } = oidcConfig();

	if (!end_session_endpoint) {
		redirect(303, '/auth/login');
	}

	const url = new URL(end_session_endpoint);
	url.searchParams.set('client_id', clientId);
	url.searchParams.set('post_logout_redirect_uri', `${settings().origin}/auth/login`);

	redirect(303, url.toString(), { external: [url.origin] });
};
