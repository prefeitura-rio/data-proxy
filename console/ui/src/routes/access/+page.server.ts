import * as v from 'valibot';
import { error, fail } from '@sveltejs/kit';
import { text } from '#lib/forms.js';
import { containsFilter, pageOf, rangeQuery } from '#lib/list.js';
import { can } from '#lib/permissions.js';
import { writeMessage } from '#lib/messages.js';
import { api, need } from '#lib/server/api.js';
import type { Result } from '#lib/server/postgrest.js';
import { AccessLogRow, PolicyRow, SchemaName } from '#lib/types.js';
import type { Actions, PageServerLoad } from './$types';

const PAGE = 50;
const LOG_PAGE = 20;
const ACTIONS = ['insert', 'update', 'delete'];

export const load: PageServerLoad = async ({ locals, parent, url }) => {
	const { permissions } = await parent();

	if (!can(permissions, 'read_policy')) {
		error(403, "You aren't allowed to do this.");
	}

	const client = api(locals.session);
	const schemas = need(await client.get('/schemas?select=name&order=name', v.array(SchemaName))).map((row) => row.name);
	const schema = url.searchParams.get('schema') ?? schemas[0] ?? null;

	if (schema === null || !schemas.includes(schema)) {
		return {
			schemas,
			schema: null,
			policy: [],
			policyTotal: 0,
			policyPage: pageOf(url.searchParams, PAGE),
			log: [],
			logTotal: 0,
			logPage: pageOf(url.searchParams, LOG_PAGE, 'log_page'),
			can: { create: false, edit: false, delete: false }
		};
	}

	const policyPage = pageOf(url.searchParams, PAGE);
	const logPage = pageOf(url.searchParams, LOG_PAGE, 'log_page');
	const action = url.searchParams.get('action') ?? '';
	const actionFilter = ACTIONS.includes(action) ? `&action=eq.${action}` : '';
	const [policy, log] = await Promise.all([
		client.page(
			`/access_policy?order=subject,unit_type,unit_id&${rangeQuery(policyPage)}${containsFilter(['subject', 'unit_id'], url.searchParams.get('q') ?? '')}`,
			v.array(PolicyRow),
			schema
		),
		client.page(
			`/access_log?order=changed_at.desc&${rangeQuery(logPage)}${actionFilter}${containsFilter(['subject'], url.searchParams.get('log_subject') ?? '')}`,
			v.array(AccessLogRow),
			schema
		)
	]);
	const policyRows = need(policy);
	const logRows = need(log);

	return {
		schemas,
		schema,
		policy: policyRows.rows,
		policyTotal: policyRows.total,
		policyPage,
		log: logRows.rows,
		logTotal: logRows.total,
		logPage,
		can: {
			create: can(permissions, 'create_policy'),
			edit: can(permissions, 'edit_policy'),
			delete: can(permissions, 'delete_policy')
		}
	};
};

const done = (result: Result<unknown>) =>
	result.ok ? { saved: true } : fail(result.status, { message: writeMessage(result) });

const match = (form: FormData, prefix: string) =>
	['subject', 'unit_type', 'unit_id']
		.map((column) => `${column}=eq.${encodeURIComponent(text(form, `${prefix}${column}`))}`)
		.join('&');

export const actions: Actions = {
	add: async ({ locals, request, url }) => {
		const form = await request.formData();

		return done(
			await api(locals.session).post(
				'/access_policy',
				{ subject: text(form, 'subject'), unit_type: text(form, 'unit_type'), unit_id: text(form, 'unit_id') },
				url.searchParams.get('schema') ?? undefined
			)
		);
	},
	edit: async ({ locals, request, url }) => {
		const form = await request.formData();

		return done(
			await api(locals.session).patch(
				`/access_policy?${match(form, 'old_')}`,
				{ subject: text(form, 'subject'), unit_type: text(form, 'unit_type'), unit_id: text(form, 'unit_id') },
				url.searchParams.get('schema') ?? undefined
			)
		);
	},
	remove: async ({ locals, request, url }) => {
		const form = await request.formData();

		return done(
			await api(locals.session).delete(
				`/access_policy?${match(form, 'old_')}`,
				url.searchParams.get('schema') ?? undefined
			)
		);
	}
};
