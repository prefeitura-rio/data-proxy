import * as v from 'valibot';
import { error } from '@sveltejs/kit';
import { api, need } from '#lib/server/api.js';
import { LogLine, Run } from '#lib/types.js';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ locals, params, url }) => {
	const client = api(locals.session);
	const id = encodeURIComponent(params.id);
	const after = url.searchParams.get('after');
	const filter = after ? `&logged_at=gte.${encodeURIComponent(after)}` : '';
	const [logs, runs] = await Promise.all([
		client.get(`/logs?run_id=eq.${id}${filter}&order=logged_at.asc&limit=1000`, v.array(LogLine)),
		client.get(`/runs?id=eq.${id}`, v.array(Run))
	]);
	const run = need(runs)[0];

	if (!run) {
		error(404, 'No such run');
	}

	return Response.json({ logs: need(logs), status: run.status });
};
