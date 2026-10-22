<script lang="ts">
	import * as v from 'valibot';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { lastLoggedAt, mergeLogs } from '#lib/logs.js';
	import { controlsFor } from '#lib/runs.js';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import Section from '#lib/components/Section.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import Table from '#lib/components/Table.svelte';
	import { LogsAnswer } from '#lib/types.js';
	import type { PageProps } from './$types';
	import RunControls from './RunControls.svelte';
	import { ArrowLeft } from '@lucide/svelte';

	let { data, form }: PageProps = $props();

	let polled = $state<{ runId: string; logs: typeof data.logs; status: string } | null>(null);

	const current = $derived(polled?.runId === data.run.id ? polled : null);
	const logs = $derived(current?.logs ?? data.logs);
	const status = $derived(current?.status ?? data.run.status);
	let watching = $state(false);

	const running = $derived(['PENDING', 'ENQUEUED', 'DELAYED'].includes(status));
	const controls = $derived(controlsFor(status, data.permissions));

	function watch() {
		watching = true;
		setTimeout(() => {
			watching = false;
		}, 30_000);
	}

	async function refresh() {
		const after = lastLoggedAt(logs);
		const response = await fetch(`/sync/runs/${data.run.id}/logs${after ? `?after=${encodeURIComponent(after)}` : ''}`);

		if (response.ok) {
			const body = v.parse(LogsAnswer, await response.json());
			polled = { runId: data.run.id, logs: mergeLogs(logs, body.logs), status: body.status };
		}
	}

	$effect(() => {
		if (!running && !watching) {
			return;
		}

		const timer = setInterval(() => void refresh(), 3000);

		return () => {
			clearInterval(timer);
		};
	});
</script>

<a class="mb-3 inline-flex link items-center gap-1 text-sm link-hover" href={resolve('/sync')}
	><ArrowLeft size={14} /> Back to runs</a
>
<PageHeader title="Run {data.run.id}" />
<p class="mb-4 flex flex-wrap items-center gap-1">
	Status: <StatusBadge {status} /> Started by {data.run.started_by ?? data.run.schedule_name ?? 'unknown'}.
</p>
<RunControls {controls} {form} onsent={watch} />

<Section title="Tasks">
	<Table head={['Task', 'Schema', 'Status', 'Created']} small>
		{#each data.tasks as task (task.id)}
			<tr>
				<td>{task.name}</td>
				<td>{task.schema_name ?? ''}</td>
				<td><StatusBadge status={task.status} /></td>
				<td>{task.created_at}</td>
			</tr>
		{/each}
	</Table>
	<Pager
		page={data.tasksPage}
		shown={data.tasks.length}
		total={data.tasksTotal}
		action={resolve('/sync/runs/[id]', { id: data.run.id })}
		params={page.url.searchParams}
		pageKey="tasks_page"
		pageKeys={['tasks_page']}
	/>
</Section>

<Section title="Steps">
	<Table head={['Step', 'Function', 'Failed', 'Started', 'Completed']} small>
		{#each data.steps as step (`${step.workflow_id}:${step.function_id}`)}
			<tr>
				<td>{step.function_id}</td>
				<td>{step.function_name}</td>
				<td
					>{#if step.failed}<span class="badge badge-soft badge-sm badge-error">yes</span>{:else}no{/if}</td
				>
				<td>{step.started_at ?? ''}</td>
				<td>{step.completed_at ?? ''}</td>
			</tr>
		{/each}
	</Table>
	<Pager
		page={data.stepsPage}
		shown={data.steps.length}
		total={data.stepsTotal}
		action={resolve('/sync/runs/[id]', { id: data.run.id })}
		params={page.url.searchParams}
		pageKey="steps_page"
		pageKeys={['steps_page']}
	/>
</Section>

<Section title="Log">
	<pre
		class="max-h-96 overflow-auto rounded-box border border-base-300 bg-base-200 p-3 font-mono text-xs leading-relaxed">{#each logs as line (`${line.workflow_id}:${line.position}`)}{line.logged_at} {line.level} {line.message}
		{/each}</pre>
	{#if data.logsTotal !== null && data.logsTotal > logs.length}
		<form method="GET" action={resolve('/sync/runs/[id]', { id: data.run.id })} class="flex items-center gap-2 text-sm">
			{#each [...page.url.searchParams].filter(([key]) => key !== 'lines') as [key, value] (key)}
				<input type="hidden" name={key} {value} />
			{/each}
			<span>Showing {logs.length} of {data.logsTotal} lines.</span>
			<button class="btn btn-sm" type="submit" name="lines" value={data.lines + 500}>Load more lines</button>
		</form>
	{/if}
</Section>
