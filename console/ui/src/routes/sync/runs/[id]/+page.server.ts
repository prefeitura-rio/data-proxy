import * as v from 'valibot';
import { error, fail, redirect } from '@sveltejs/kit';
import { pageOf, rangeQuery } from '#lib/list.js';
import { controlMessage } from '#lib/messages.js';
import { can } from '#lib/permissions.js';
import { api, need } from '#lib/server/api.js';
import { LogLine, Run, Step, Task } from '#lib/types.js';
import type { Actions, PageServerLoad } from './$types';

const PAGE = 50;
const LINES = 500;
const MAX_LINES = 5000;

export const load: PageServerLoad = async ({ locals, parent, params, url }) => {
	const { permissions } = await parent();

	if (!can(permissions, 'read_sync')) {
		error(403, "You aren't allowed to do this.");
	}

	const client = api(locals.session);
	const id = encodeURIComponent(params.id);
	const tasksPage = pageOf(url.searchParams, PAGE, 'tasks_page');
	const stepsPage = pageOf(url.searchParams, PAGE, 'steps_page');
	const asked = Number(url.searchParams.get('lines') ?? LINES);
	const lines = Number.isInteger(asked) && asked >= LINES ? Math.min(asked, MAX_LINES) : LINES;
	const [runs, tasks, steps, logs] = await Promise.all([
		client.get(`/runs?id=eq.${id}`, v.array(Run)),
		client.page(`/tasks?run_id=eq.${id}&order=created_at&${rangeQuery(tasksPage)}`, v.array(Task)),
		client.page(`/steps?run_id=eq.${id}&order=started_at,function_id&${rangeQuery(stepsPage)}`, v.array(Step)),
		client.page(`/logs?run_id=eq.${id}&order=logged_at.asc&limit=${lines}`, v.array(LogLine))
	]);
	const run = need(runs)[0];

	if (!run) {
		error(404, 'No such run');
	}

	const taskRows = need(tasks);
	const stepRows = need(steps);
	const logRows = need(logs);

	return {
		run,
		permissions,
		tasks: taskRows.rows,
		tasksTotal: taskRows.total,
		tasksPage,
		steps: stepRows.rows,
		stepsTotal: stepRows.total,
		stepsPage,
		logs: logRows.rows,
		logsTotal: logRows.total,
		lines
	};
};

const control =
	(name: 'cancel_run' | 'resume_run', done: string): Actions[string] =>
	async ({ locals, params }) => {
		const result = await api(locals.session).rpc(name, v.unknown(), { run: params.id });

		return result.ok ? { control: done } : fail(result.status, { message: controlMessage(result) ?? result.message });
	};

export const actions: Actions = {
	cancel: control('cancel_run', 'Cancel requested. The run stops at its next step.'),
	resume: control('resume_run', 'Resume requested. The run continues from its last finished step.'),
	retry: async ({ locals, params }) => {
		const result = await api(locals.session).rpc('retry_run', v.string(), { run: params.id });

		if (!result.ok) {
			return fail(result.status, { message: controlMessage(result) ?? result.message });
		}

		redirect(303, `/sync/runs/${encodeURIComponent(result.data)}`);
	}
};
