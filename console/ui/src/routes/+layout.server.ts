import * as v from 'valibot';
import { error, redirect } from '@sveltejs/kit';
import { identityOf } from '#lib/identity.js';
import { tabsFor } from '#lib/permissions.js';
import { api } from '#lib/server/api.js';
import { clearSession } from '#lib/server/auth.js';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ locals, cookies, url }) => {
	const identity = identityOf(locals.session.accessToken);

	// PostgREST takes a token without a role for an anonymous request and answers 401. Signing in again gives the same
	// token, so the page would loop between the console and the identity provider.
	if (identity?.role === '') {
		if (url.pathname !== '/no-access') {
			redirect(303, '/no-access');
		}

		return { permissions: [], tabs: [], identity };
	}

	const result = await api(locals.session).get('/rpc/permissions', v.array(v.string()));

	if (!result.ok && result.status === 401) {
		clearSession(cookies);
		redirect(303, `/auth/login?next=${encodeURIComponent(url.pathname)}`);
	}

	if (!result.ok) {
		error(result.status >= 400 && result.status < 600 ? result.status : 502, result.message);
	}

	return {
		permissions: result.data,
		tabs: tabsFor(result.data),
		identity
	};
};
