import { error } from '@sveltejs/kit';
import * as v from 'valibot';
import { can } from '#lib/permissions.js';
import { api, need } from '#lib/server/api.js';
import { PolicyRow, SchemaName } from '#lib/types.js';
import type { RequestHandler } from './$types';

const MAX_GRANTS = 500;

/** Read the grants of one subject in one schema, for the panel that explains the row rules. */
export const GET: RequestHandler = async ({ locals, url }) => {
	const subject = url.searchParams.get('subject')?.trim() ?? '';
	const schema = url.searchParams.get('schema') ?? '';

	if (subject === '' || schema === '') {
		error(400, 'Give a subject and a schema.');
	}

	const client = api(locals.session);
	const permissions = need(await client.get('/rpc/permissions', v.array(v.string())));

	if (!can(permissions, 'read_policy')) {
		error(403, "You aren't allowed to do this.");
	}

	const schemas = need(await client.get('/schemas?select=name&order=name', v.array(SchemaName))).map((row) => row.name);

	if (!schemas.includes(schema)) {
		error(404, 'No such schema.');
	}

	const rows = need(
		await client.get(
			`/access_policy?subject=eq.${encodeURIComponent(subject)}&order=unit_type,unit_id&limit=${MAX_GRANTS}`,
			v.array(PolicyRow),
			schema
		)
	);

	return Response.json(rows);
};
