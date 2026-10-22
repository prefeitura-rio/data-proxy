import * as v from 'valibot';
import { error, fail } from '@sveltejs/kit';
import { list, optional, text, whole } from '#lib/forms.js';
import { containsFilter, pageOf, rangeQuery } from '#lib/list.js';
import { can } from '#lib/permissions.js';
import { writeMessage } from '#lib/messages.js';
import { api, need } from '#lib/server/api.js';
import type { Result } from '#lib/server/postgrest.js';
import { HistoryRow, Health, ImportChange, RlsRow, SchemaRow, Synced, TableName, TableRow } from '#lib/types.js';
import type { Actions, PageServerLoad } from './$types';

const TABLES_PAGE = 25;
const HISTORY_PAGE = 25;
const OBJECTS = ['schemas', 'tables', 'rls'];

export const load: PageServerLoad = async ({ locals, parent, url }) => {
	const { permissions } = await parent();

	if (!can(permissions, 'read_config')) {
		error(403, "You aren't allowed to do this.");
	}

	const client = api(locals.session);
	const tablesPage = pageOf(url.searchParams, TABLES_PAGE, 'tables_page');
	const historyPage = pageOf(url.searchParams, HISTORY_PAGE, 'history_page');
	const [schemas, tableNames, rls, health] = await Promise.all([
		client.get('/schemas?order=name', v.array(SchemaRow)),
		client.get('/tables?select=name,schema&order=schema,name', v.array(TableName)),
		client.get('/rls?order=table_name,column_name', v.array(RlsRow)),
		client.get('/health?order=name', v.array(Health))
	]);
	const schemaRows = need(schemas);
	const schemaFilter = schemaRows.some((row) => row.name === url.searchParams.get('table_schema'))
		? `&schema=eq.${encodeURIComponent(url.searchParams.get('table_schema') ?? '')}`
		: '';
	const historySchema = url.searchParams.get('history_schema') ?? '';
	const historyObject = url.searchParams.get('history_object') ?? '';
	const historyFilter =
		(schemaRows.some((row) => row.name === historySchema)
			? `&schema_name=eq.${encodeURIComponent(historySchema)}`
			: '') + (OBJECTS.includes(historyObject) ? `&object=eq.${historyObject}` : '');
	const history = need(
		await client.page(
			`/config_history?order=changed_at.desc,id.desc&${rangeQuery(historyPage)}${historyFilter}`,
			v.array(HistoryRow)
		)
	);
	const shown = need(
		await client.page(
			`/tables?order=schema,name&${rangeQuery(tablesPage)}${schemaFilter}${containsFilter(['name'], url.searchParams.get('table_q') ?? '')}`,
			v.array(TableRow)
		)
	);
	const synced =
		shown.rows.length === 0
			? []
			: need(
					await client.get(
						`/synced?name=in.(${shown.rows.map((row) => encodeURIComponent(`"${row.name}"`)).join(',')})&order=schema,name`,
						v.array(Synced)
					)
				);

	return {
		schemas: schemaRows,
		tables: shown.rows,
		tablesTotal: shown.total,
		tablesPage,
		history: history.rows,
		historyTotal: history.total,
		historyPage,
		tableNames: need(tableNames),
		rls: need(rls),
		health: need(health),
		synced,
		can: {
			create: can(permissions, 'create_config'),
			edit: can(permissions, 'edit_config'),
			delete: can(permissions, 'delete_config'),
			import: can(permissions, 'import_config')
		}
	};
};

const done = (result: Result<unknown>) =>
	result.ok ? { saved: true } : fail(result.status, { message: writeMessage(result) });

/** Read the document of an import from the uploaded file, or from the text that the preview kept. */
async function documentText(form: FormData): Promise<string | null> {
	const file = form.get('document');

	if (file instanceof File && file.size > 0) {
		return file.text();
	}

	const kept = form.get('document_text');

	return typeof kept === 'string' && kept !== '' ? kept : null;
}

function parseJson(text: string): unknown {
	try {
		return JSON.parse(text);
	} catch {
		return undefined;
	}
}

export const actions: Actions = {
	createSchema: async ({ locals, request }) => {
		const form = await request.formData();

		return done(
			await api(locals.session).post('/schemas', {
				name: text(form, 'name'),
				source: text(form, 'source') || 'bigquery',
				claim: optional(form, 'claim'),
				encrypted: form.get('encrypted') === 'on'
			})
		);
	},
	updateSchema: async ({ locals, request }) => {
		const form = await request.formData();
		const name = encodeURIComponent(text(form, 'name'));

		return done(
			await api(locals.session).patch(`/schemas?name=eq.${name}`, {
				claim: optional(form, 'claim'),
				encrypted: form.get('encrypted') === 'on'
			})
		);
	},
	deleteSchema: async ({ locals, request }) => {
		const form = await request.formData();
		const name = text(form, 'name');

		if (text(form, 'confirm') !== name) {
			return fail(400, { message: 'Type the name of the schema to delete it.' });
		}

		return done(await api(locals.session).delete(`/schemas?name=eq.${encodeURIComponent(name)}`));
	},
	createTable: async ({ locals, request }) => {
		const form = await request.formData();
		const strategy = text(form, 'strategy') || 'full';

		return done(
			await api(locals.session).post('/tables', {
				name: text(form, 'name'),
				schema: text(form, 'schema'),
				strategy,
				fallback: form.get('fallback') === 'on'
			})
		);
	},
	updateTable: async ({ locals, request }) => {
		const form = await request.formData();
		const name = encodeURIComponent(text(form, 'name'));

		return done(
			await api(locals.session).patch(`/tables?name=eq.${name}`, {
				strategy: text(form, 'strategy') || 'full',
				n: whole(form, 'n'),
				fallback: form.get('fallback') === 'on',
				cache_ttl: whole(form, 'cache_ttl'),
				sort: list(form, 'sort')
			})
		);
	},
	deleteTable: async ({ locals, request }) => {
		const name = encodeURIComponent(text(await request.formData(), 'name'));

		return done(await api(locals.session).delete(`/tables?name=eq.${name}`));
	},
	createRls: async ({ locals, request }) => {
		const form = await request.formData();

		return done(
			await api(locals.session).post('/rls', {
				table_name: text(form, 'table_name'),
				column_name: text(form, 'column_name'),
				unit_type: text(form, 'unit_type')
			})
		);
	},
	updateRls: async ({ locals, request }) => {
		const form = await request.formData();
		const id = encodeURIComponent(text(form, 'id'));

		return done(
			await api(locals.session).patch(`/rls?id=eq.${id}`, {
				column_name: text(form, 'column_name'),
				unit_type: text(form, 'unit_type')
			})
		);
	},
	deleteRls: async ({ locals, request }) => {
		const id = encodeURIComponent(text(await request.formData(), 'id'));

		return done(await api(locals.session).delete(`/rls?id=eq.${id}`));
	},
	preview: async ({ locals, request }) => {
		const text = await documentText(await request.formData());

		if (text === null) {
			return fail(400, { message: 'Choose a sync.json file.' });
		}

		const document = parseJson(text);

		if (document === undefined) {
			return fail(400, { message: 'The file is not valid JSON.' });
		}

		const result = await api(locals.session).rpc('import_preview', v.array(ImportChange), { document });

		return result.ok
			? { preview: result.data, documentText: text }
			: fail(result.status, { message: writeMessage(result) });
	},
	import: async ({ locals, request }) => {
		const text = await documentText(await request.formData());

		if (text === null) {
			return fail(400, { message: 'Choose a sync.json file.' });
		}

		const document = parseJson(text);

		if (document === undefined) {
			return fail(400, { message: 'The file is not valid JSON.' });
		}

		return done(await api(locals.session).rpc('import_config', v.unknown(), { document }));
	}
};
