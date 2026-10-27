import * as v from 'valibot';
import { api, need } from '#lib/server/api.js';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ locals }) => {
	const document = need(await api(locals.session).get('/rpc/sync_config', v.unknown()));

	return new Response(JSON.stringify(document, null, 2), {
		headers: {
			'Content-Type': 'application/json',
			'Content-Disposition': 'attachment; filename="sync.json"'
		}
	});
};
