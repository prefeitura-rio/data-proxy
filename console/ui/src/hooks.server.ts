import { error, redirect } from '@sveltejs/kit';
import type { Handle } from '@sveltejs/kit/hooks';
import { freshSession } from '#lib/server/auth.js';
import { isSecure, sameOrigin } from '#lib/server/origin.js';
import { settings } from '#lib/server/settings.js';

export const handle: Handle = async ({ event, resolve }) => {
	if (!sameOrigin(event.request.method, event.request.headers.get('origin'), settings().origin)) {
		error(403, 'The request does not come from this site');
	}

	if (event.url.pathname !== '/healthz' && !event.url.pathname.startsWith('/auth/')) {
		const session = await freshSession(event.cookies, isSecure(settings().origin));

		if (session === null) {
			redirect(303, `/auth/login?next=${encodeURIComponent(event.url.pathname + event.url.search)}`);
		}

		event.locals.session = session;
	}

	return resolve(event);
};
