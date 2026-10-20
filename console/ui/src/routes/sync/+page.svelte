<script lang="ts">
	import { enhance } from '$app/forms';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { Play } from '@lucide/svelte';
	import Check from '#lib/components/Check.svelte';
	import Field from '#lib/components/Field.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import Table from '#lib/components/Table.svelte';
	import RunFilters from './RunFilters.svelte';
	import ScheduleSection from './ScheduleSection.svelte';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();
</script>

<h1 class="mb-4 text-2xl font-semibold">Sync</h1>

<section class="mb-6 grid gap-4 md:grid-cols-3">
	{#each data.overview as card (card.name)}
		<div class="card bg-base-100 card-border">
			<div class="card-body">
				<h2 class="card-title">{card.name}</h2>
				<p>{card.synced_tables} of {card.tables} tables synced</p>
				<p>Last run: {card.last_run_status ?? 'none'}</p>
				<p>
					{card.runs_24h}
					{card.runs_24h === 1 ? 'run' : 'runs'}, {card.failed_runs_24h} failed, {card.errors_24h}
					{card.errors_24h === 1 ? 'error' : 'errors'} in 24 hours
				</p>
			</div>
		</div>
	{/each}
</section>

{#if data.canStart}
	<section class="mb-6">
		<h2 class="mb-2 text-lg font-semibold">Start run</h2>
		<form method="POST" action="?/start" use:enhance class="flex flex-col gap-2">
			<fieldset class="flex flex-wrap gap-4">
				{#each data.overview as card (card.name)}
					<Check label={card.name} name="schemas" value={card.name} />
				{/each}
			</fieldset>
			<textarea
				class="textarea w-full"
				name="tables"
				rows="2"
				placeholder="Tables, one on each line. Empty means every table."
				aria-label="Tables"></textarea>
			<div>
				<button class="btn btn-primary" type="submit"><Play size={16} /> Start run</button>
			</div>
			{#if form?.message}<p role="alert" class="text-error">{form.message}</p>{/if}
			{#if form?.started}<p role="status" class="text-success">Run {form.started} started.</p>{/if}
		</form>
	</section>
{/if}

<section class="mb-6">
	<h2 class="mb-2 text-lg font-semibold">Runs</h2>
	<RunFilters params={page.url.searchParams} schemas={data.overview.map((card) => card.name)} />
	<Table head={['Run', 'Status', 'Started by', 'Created', 'Tasks']}>
		{#each data.runs as run (run.id)}
			<tr>
				<td><a class="link" href={resolve('/sync/runs/[id]', { id: run.id })}>{run.id.slice(0, 8)}</a></td>
				<td>{run.status}</td>
				<td>{run.started_by ?? run.schedule_name ?? ''}</td>
				<td>{run.created_at}</td>
				<td>{run.task_count} ({run.failed_task_count} failed)</td>
			</tr>
		{/each}
	</Table>
	<Pager
		page={data.page}
		shown={data.runs.length}
		total={data.runsTotal}
		action={resolve('/sync')}
		params={page.url.searchParams}
	/>
</section>

<section class="mb-6 grid gap-6 md:grid-cols-2">
	<div>
		<h2 class="mb-2 text-lg font-semibold">Queues</h2>
		<Table head={['Name', 'Enqueued', 'Pending', 'Delayed']} small>
			{#each data.queues as queue (queue.name)}
				<tr><td>{queue.name}</td><td>{queue.enqueued}</td><td>{queue.pending}</td><td>{queue.delayed}</td></tr>
			{/each}
		</Table>
	</div>
	<div>
		<h2 class="mb-2 text-lg font-semibold">Schedules</h2>
		<ScheduleSection
			schedules={data.schedules}
			canControl={data.canControl}
			scheduled={form?.scheduled}
			problem={form?.scheduleMessage}
		/>
	</div>
</section>

<section>
	<h2 class="mb-2 text-lg font-semibold">Errors</h2>
	<form method="GET" action={resolve('/sync')} class="mb-2 flex items-end gap-2" aria-label="Filter the errors">
		{#each [...page.url.searchParams].filter(([key]) => key !== 'reason' && key !== 'errors_page') as [key, value] (key)}
			<input type="hidden" name={key} {value} />
		{/each}
		<Field label="Reason contains" name="reason" value={page.url.searchParams.get('reason') ?? ''} />
		<button class="btn btn-primary btn-sm" type="submit">Filter</button>
	</form>
	<Table head={['Time', 'Reason', 'Fields']} small>
		{#each data.errors as row (row.id)}
			<tr><td>{row.recorded_at}</td><td>{row.reason}</td><td><code>{JSON.stringify(row.fields)}</code></td></tr>
		{/each}
	</Table>
	<Pager
		page={data.errorsPage}
		shown={data.errors.length}
		total={data.errorsTotal}
		action={resolve('/sync')}
		params={page.url.searchParams}
		pageKey="errors_page"
		pageKeys={['errors_page']}
	/>
</section>
