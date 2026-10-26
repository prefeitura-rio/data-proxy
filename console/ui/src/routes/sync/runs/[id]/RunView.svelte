<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { ArrowLeft, Copy } from '@lucide/svelte';
	import LogViewer from '#lib/components/LogViewer.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import RefreshControl from '#lib/components/RefreshControl.svelte';
	import RunProgress from '#lib/components/RunProgress.svelte';
	import Section from '#lib/components/Section.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import Table from '#lib/components/Table.svelte';
	import Time from '#lib/components/Time.svelte';
	import { clock } from '#lib/clock.svelte.js';
	import { poll } from '#lib/poll.svelte.js';
	import { controlsFor } from '#lib/runs.js';
	import { isActive } from '#lib/status.js';
	import { runStream } from '#lib/stream.svelte.js';
	import { success } from '#lib/toast.svelte.js';
	import type { PageProps } from './$types';
	import RunControls from './RunControls.svelte';

	let { data, form }: { data: PageProps['data']; form: PageProps['form'] } = $props();

	const refresher = poll('app:run');
	const stream = runStream({ id: () => data.run.id, logs: () => data.logs, status: () => data.run.status });
	const clocked = clock(() => isActive(stream.status));
	const controls = $derived(controlsFor(stream.status, data.permissions));

	async function copyId() {
		await navigator.clipboard.writeText(data.run.id);
		success('Run id copied.');
	}
</script>

<a class="mb-3 inline-flex link items-center gap-1 text-sm link-hover" href={resolve('/sync')}
	><ArrowLeft size={14} /> Back to runs</a
>
<PageHeader title="Run {data.run.id}" label="Run {data.run.id.slice(0, 8)}">
	<button class="btn btn-ghost btn-sm" type="button" onclick={copyId}><Copy size={14} /> Copy id</button>
	<RefreshControl {refresher} />
</PageHeader>
<p class="mb-4 flex flex-wrap items-center gap-1">
	Status: <StatusBadge status={stream.status} /> Started by {data.run.started_by ??
		data.run.schedule_name ??
		'unknown'}.
	{#if stream.live}
		<span class="badge badge-soft badge-sm {stream.connection === 'live' ? 'badge-success' : 'badge-warning'}">
			{stream.connection === 'live' ? 'Live' : 'Reconnecting'}
		</span>
	{/if}
</p>
<RunProgress run={data.run} status={stream.status} tasks={data.tasks} total={data.tasksTotal} now={clocked.now} />
<RunControls
	{controls}
	{form}
	onsent={() => {
		stream.watch();
	}}
/>

<Section title="Tasks">
	<Table
		head={['Task', 'Schema', 'Status', 'Created']}
		small
		caption="Tasks"
		count={data.tasks.length}
		empty="This run has no task yet."
	>
		{#each data.tasks as task (task.id)}
			<tr>
				<td>{task.name}</td>
				<td>{task.schema_name ?? ''}</td>
				<td><StatusBadge status={task.status} /></td>
				<td><Time value={task.created_at} /></td>
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
	<Table
		head={['Step', 'Function', 'Failed', 'Started', 'Completed']}
		small
		caption="Steps"
		count={data.steps.length}
		empty="This run has no step yet."
	>
		{#each data.steps as step (`${step.workflow_id}:${step.function_id}`)}
			<tr>
				<td>{step.function_id}</td>
				<td>{step.function_name}</td>
				<td
					>{#if step.failed}<span class="badge badge-soft badge-sm badge-error">yes</span>{:else}no{/if}</td
				>
				<td><Time value={step.started_at} /></td>
				<td><Time value={step.completed_at} /></td>
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
	{#if isActive(stream.status) || stream.live}
		<label class="flex cursor-pointer items-center gap-2 self-start text-sm">
			<input type="checkbox" class="toggle toggle-primary toggle-sm" bind:checked={stream.live} />
			Live logs
		</label>
	{/if}
	<LogViewer lines={stream.logs} following={stream.live} name="run-{data.run.id.slice(0, 8)}" />
	{#if data.logsTotal !== null && data.logsTotal > stream.logs.length}
		<form method="GET" action={resolve('/sync/runs/[id]', { id: data.run.id })} class="flex items-center gap-2 text-sm">
			{#each [...page.url.searchParams].filter(([key]) => key !== 'lines') as [key, value] (key)}
				<input type="hidden" name={key} {value} />
			{/each}
			<span>Showing {stream.logs.length} of {data.logsTotal} lines.</span>
			<button class="btn btn-sm" type="submit" name="lines" value={data.lines + 500}>Load more lines</button>
		</form>
	{/if}
</Section>
