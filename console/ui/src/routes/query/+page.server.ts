import * as v from 'valibot';
import { error } from '@sveltejs/kit';
import { can } from '#lib/permissions.js';
import { api, need } from '#lib/server/api.js';
import { queryView } from '#lib/server/query-view.js';
import { querySettings } from '#lib/server/settings.js';
import { SchemaFlags, TableName } from '#lib/types.js';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, parent }) => {
	const { permissions } = await parent();

	if (!can(permissions, 'read_files')) {
		error(403, "You aren't allowed to do this.");
	}

	const client = api(locals.session);
	const [schemas, tables] = await Promise.all([
		client.get('/schemas?select=name,encrypted&order=name', v.array(SchemaFlags)),
		client.get('/tables?select=name,schema&order=schema,name', v.array(TableName))
	]);

	return {
		schemas: need(schemas),
		tables: need(tables),
		...queryView(querySettings())
	};
};
