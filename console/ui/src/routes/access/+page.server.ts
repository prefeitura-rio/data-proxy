import * as v from 'valibot';
import { error, fail } from '@sveltejs/kit';
import { text } from '#lib/forms.js';
import { containsFilter, parsePage, rangeQuery } from '#lib/list.js';
import { parseUnits } from '#lib/policy.js';
import { can } from '#lib/permissions.js';
import { writeMessage } from '#lib/messages.js';
import { api, need } from '#lib/server/api.js';
import type { Result } from '#lib/server/postgrest.js';
import { AccessLogRow, CatalogPath, PolicyRow, ProjectRow } from '#lib/types.js';
import type { Actions, PageServerLoad } from './$types';

const PAGE = 50;
const LOG_PAGE = 20;
const ACTIONS = ['insert', 'update', 'delete'];
const ACCESS = 'access';

/** The scope that stands for every project and every schema: the empty ltree path. */
const ALL = '*';

const toPath = (scope: string) => (scope === ALL ? '' : scope);
const ProjectName = v.pick(ProjectRow, ['name']);

export const load: PageServerLoad = async ({ locals, parent, url }) => {
	const { permissions } = await parent();

	if (!can(permissions, 'read_policy')) {
		error(403, "You aren't allowed to do this.");
	}

	const client = api(locals.session);
	const [projects, catalog] = await Promise.all([
		client.get('/projects?select=name&order=name', v.array(ProjectName)),
		client.get('/catalog?select=path&path=not.is.null&order=path', v.array(CatalogPath))
	]);
	const scopes = [
		...(can(permissions, 'grant_all') ? [ALL] : []),
		...need(projects).map((row) => row.name),
		...need(catalog).map((row) => row.path)
	];
	const scope = url.searchParams.get('scope') ?? scopes[0] ?? null;

	if (scope === null || !scopes.includes(scope)) {
		return {
			scopes,
			scope: null,
			policy: [],
			policyTotal: 0,
			policyPage: parsePage(url.searchParams, PAGE),
			log: [],
			logTotal: 0,
			logPage: parsePage(url.searchParams, LOG_PAGE, 'log_page'),
			can: { create: false, edit: false, delete: false }
		};
	}

	const policyPage = parsePage(url.searchParams, PAGE);
	const logPage = parsePage(url.searchParams, LOG_PAGE, 'log_page');
	const action = url.searchParams.get('action') ?? '';
	const actionFilter = ACTIONS.includes(action) ? `&action=eq.${action}` : '';
	const scopeFilter = `&scope=eq.${encodeURIComponent(toPath(scope))}`;
	const [policy, log] = await Promise.all([
		client.page(
			`/policy?order=subject,unit_type,unit_id&${rangeQuery(policyPage)}${scopeFilter}${containsFilter(['subject', 'unit_id'], url.searchParams.get('q') ?? '')}`,
			v.array(PolicyRow),
			ACCESS
		),
		client.page(
			`/log?order=changed_at.desc&${rangeQuery(logPage)}${scopeFilter}${actionFilter}${containsFilter(['subject'], url.searchParams.get('log_subject') ?? '')}`,
			v.array(AccessLogRow),
			ACCESS
		)
	]);
	const policyRows = need(policy);
	const logRows = need(log);

	return {
		scopes,
		scope,
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
	result.ok ? { toast: 'Saved.' } : fail(result.status, { message: writeMessage(result) });

/** The toast of an add. Every row new gives the plain "Saved.", as every other form does. */
const addedMessage = (asked: number, added: number) =>
	added === asked ? 'Saved.' : `Saved ${added}, skipped ${asked - added} that already exist.`;

const match = (form: FormData, prefix: string) =>
	['scope', 'subject', 'unit_type', 'unit_id']
		.map((column) => `${column}=eq.${encodeURIComponent(text(form, `${prefix}${column}`))}`)
		.join('&');

export const actions: Actions = {
	add: async ({ locals, request }) => {
		const form = await request.formData();
		const units = parseUnits(text(form, 'unit_type'), text(form, 'unit_id'));

		if (!units.ok) {
			return fail(400, { message: units.message });
		}

		const scope = toPath(text(form, 'scope'));
		const subject = text(form, 'subject');
		const result = await api(locals.session).post(
			'/policy',
			units.rows.map((row) => ({ scope, subject, ...row })),
			ACCESS,
			{ skipDuplicates: ['subject', 'scope', 'unit_type', 'unit_id'] }
		);

		if (!result.ok) {
			return fail(result.status, { message: writeMessage(result) });
		}

		return { toast: addedMessage(units.rows.length, Array.isArray(result.data) ? result.data.length : 0) };
	},
	edit: async ({ locals, request }) => {
		const form = await request.formData();
		const units = parseUnits(text(form, 'unit_type'), text(form, 'unit_id'));

		if (!units.ok) {
			return fail(400, { message: units.message });
		}

		if (units.rows.length !== 1) {
			return fail(400, { message: 'Edit one unit id at a time. Use Add for a list.' });
		}

		return done(
			await api(locals.session).patch(
				`/policy?${match(form, 'old_')}`,
				{ scope: text(form, 'old_scope'), subject: text(form, 'subject'), ...units.rows[0] },
				ACCESS
			)
		);
	},
	remove: async ({ locals, request }) => {
		const form = await request.formData();

		return done(await api(locals.session).delete(`/policy?${match(form, 'old_')}`, ACCESS));
	}
};
