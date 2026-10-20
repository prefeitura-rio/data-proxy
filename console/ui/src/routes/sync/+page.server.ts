import * as v from 'valibot';
import { error, fail } from '@sveltejs/kit';
import { text } from '#lib/forms.js';
import { can } from '#lib/permissions.js';
import { api, need } from '#lib/server/api.js';
import { containsFilter, pageOf, rangeQuery } from '#lib/list.js';
import { startRunMessage } from '#lib/messages.js';
import { runFilters } from '#lib/runs.js';
import { ErrorRow, Overview, Queue, Run, Schedule } from '#lib/types.js';
import type { Actions, PageServerLoad } from './$types';

const PAGE = 25;
const ERRORS_PAGE = 10;

export const load: PageServerLoad = async ({ locals, parent, url }) => {
	const { permissions } = await parent();

	if (!can(permissions, 'read_sync')) {
		error(403, "You aren't allowed to do this.");
	}

	const client = api(locals.session);
	const page = pageOf(url.searchParams, PAGE);
	const errorsPage = pageOf(url.searchParams, ERRORS_PAGE, 'errors_page');
	const [overview, runs, queues, schedules, errors] = await Promise.all([
		client.get('/overview?order=name', v.array(Overview)),
		client.page(`/runs?order=created_at.desc&${rangeQuery(page)}${runFilters(url.searchParams)}`, v.array(Run)),
		client.get('/queues?order=name', v.array(Queue)),
		client.get('/schedules?order=name', v.array(Schedule)),
		client.page(
			`/errors?${rangeQuery(errorsPage)}${containsFilter(['reason'], url.searchParams.get('reason') ?? '')}`,
			v.array(ErrorRow)
		)
	]);
	const runRows = need(runs);
	const errorRows = need(errors);

	return {
		overview: need(overview),
		runs: runRows.rows,
		runsTotal: runRows.total,
		page,
		errorsPage,
		queues: need(queues),
		schedules: need(schedules),
		errors: errorRows.rows,
		errorsTotal: errorRows.total,
		canStart: can(permissions, 'start_sync'),
		canControl: can(permissions, 'control_sync')
	};
};

const schedule =
	(name: 'pause_schedule' | 'resume_schedule', done: string): Actions[string] =>
	async ({ locals, request }) => {
		const result = await api(locals.session).rpc(name, v.unknown(), {
			schedule_name: text(await request.formData(), 'name')
		});

		return result.ok ? { scheduled: done } : fail(result.status, { scheduleMessage: result.message });
	};

export const actions: Actions = {
	pause: schedule('pause_schedule', 'Pause requested.'),
	resume: schedule('resume_schedule', 'Resume requested.'),
	start: async ({ locals, request }) => {
		const form = await request.formData();
		const schemas = form.getAll('schemas').filter((value) => typeof value === 'string');
		const tables = text(form, 'tables')
			.split('\n')
			.map((line) => line.trim())
			.filter(Boolean);

		const result = await api(locals.session).rpc('start_sync', v.string(), { schemas, tables });

		return result.ok
			? { started: result.data }
			: fail(result.status, { message: startRunMessage(result) ?? result.message });
	}
};
