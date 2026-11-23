import { error } from '@sveltejs/kit';
import * as v from 'valibot';
import { can } from '#lib/permissions.js';
import { api, need } from '#lib/server/api.js';
import { explainPath, expandScopes } from '#lib/policy.js';
import { CatalogEntry, PolicyRow } from '#lib/types.js';
import type { RequestHandler } from './$types';

const MAX_GRANTS = 500;

/** Read the grants of one subject that cover a table, or a schema, for the panel that explains the row rules. */
export const GET: RequestHandler = async ({ locals, url }) => {
	const subject = url.searchParams.get('subject')?.trim() ?? '';
	const schema = url.searchParams.get('schema') ?? '';
	const table = url.searchParams.get('table');

	if (subject === '' || schema === '') {
		error(400, 'Give a subject and a schema.');
	}

	const client = api(locals.session);
	const permissions = need(await client.get('/rpc/permissions', v.array(v.string())));

	if (!can(permissions, 'read_policy')) {
		error(403, "You aren't allowed to do this.");
	}

	const entries = need(
		await client.get(
			`/catalog?select=table_name,path&physical_schema=eq.${encodeURIComponent(schema)}`,
			v.array(CatalogEntry)
		)
	);
	const path = explainPath(entries, table);

	if (path === null) {
		error(404, 'No such schema.');
	}

	const scopes = expandScopes(path)
		.map((scope) => `"${scope}"`)
		.join(',');
	const rows = need(
		await client.get(
			`/policy?subject=eq.${encodeURIComponent(subject)}&scope=in.(${scopes})&order=unit_type,unit_id&limit=${MAX_GRANTS}`,
			v.array(PolicyRow),
			'access'
		)
	);

	return Response.json(rows);
};
