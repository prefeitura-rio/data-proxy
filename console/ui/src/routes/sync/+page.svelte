<script lang="ts">
	import Time from '#lib/components/Time.svelte';
	import { enhance } from '$app/forms';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { Play } from '@lucide/svelte';
	import Check from '#lib/components/Check.svelte';
	import Field from '#lib/components/Field.svelte';
	import FilterForm from '#lib/components/FilterForm.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import RefreshControl from '#lib/components/RefreshControl.svelte';
	import Section from '#lib/components/Section.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import Table from '#lib/components/Table.svelte';
	import { poll } from '#lib/poll.svelte.js';
	import { Submission } from '#lib/enhance.svelte.js';
	import RunFilters from './RunFilters.svelte';
	import ScheduleSection from './ScheduleSection.svelte';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();

	const start = new Submission();
	const refresher = poll('app:sync');
</script>

<PageHeader title="Sync"><RefreshControl {refresher} /></PageHeader>

<section class="mb-8 grid gap-4 md:grid-cols-3">
	{#each data.overview as card (card.name)}
		<div class="card bg-base-100 card-border">
			<div class="card-body gap-2">
				<h2 class="card-title">{card.name}</h2>
				<p class="text-sm text-base-content/70">{card.synced_tables} of {card.tables} tables synced</p>
				<progress class="progress progress-primary" value={card.synced_tables} max={Math.max(card.tables, 1)}
				></progress>
				<p class="flex items-center gap-2 text-sm">
					Last run:
					{#if card.last_run_status}<StatusBadge status={card.last_run_status} />{:else}none{/if}
				</p>
				<p class="text-sm text-base-content/70">
					{card.runs_24h}
					{card.runs_24h === 1 ? 'run' : 'runs'}, {card.failed_runs_24h} failed, {card.errors_24h}
					{card.errors_24h === 1 ? 'error' : 'errors'} in 24 hours
				</p>
			</div>
		</div>
	{/each}
</section>

{#if data.canStart}
	<Section title="Start run">
		<form method="POST" action="?/start" use:enhance={start.submit} class="toolbar flex-col items-stretch">
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
				<SubmitButton pending={start.pending}><Play size={16} /> Start run</SubmitButton>
			</div>
			{#if form?.message}<p role="alert" class="text-error">{form.message}</p>{/if}
		</form>
	</Section>
{/if}

<Section title="Runs">
	<RunFilters params={page.url.searchParams} schemas={data.overview.map((card) => card.name)} />
	<Table
		head={['Run', 'Status', 'Started by', 'Created', 'Tasks']}
		caption="Runs"
		count={data.runs.length}
		empty="No run matches these filters."
	>
		{#each data.runs as run (run.id)}
			<tr>
				<td><a class="link" href={resolve('/sync/runs/[id]', { id: run.id })}>{run.id.slice(0, 8)}</a></td>
				<td><StatusBadge status={run.status} /></td>
				<td>{run.started_by ?? run.schedule_name ?? ''}</td>
				<td><Time value={run.created_at} /></td>
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
</Section>

<section class="mb-8 grid gap-6 md:grid-cols-2">
	<div>
		<h2 class="mb-3 text-lg font-semibold">Queues</h2>
		<Table head={['Name', 'Enqueued', 'Pending', 'Delayed']} small>
			{#each data.queues as queue (queue.name)}
				<tr><td>{queue.name}</td><td>{queue.enqueued}</td><td>{queue.pending}</td><td>{queue.delayed}</td></tr>
			{/each}
		</Table>
	</div>
	<div>
		<h2 class="mb-3 text-lg font-semibold">Schedules</h2>
		<ScheduleSection schedules={data.schedules} canControl={data.canControl} problem={form?.scheduleMessage} />
	</div>
</section>

<Section title="Errors">
	<FilterForm
		action={resolve('/sync')}
		label="Filter the errors"
		params={page.url.searchParams}
		own={['reason']}
		pages={['errors_page']}
	>
		<Field label="Reason contains" name="reason" defaultValue={page.url.searchParams.get('reason') ?? ''} />
	</FilterForm>
	<Table
		head={['Time', 'Reason', 'Fields']}
		small
		caption="Errors"
		count={data.errors.length}
		empty="No error matches this filter."
	>
		{#each data.errors as row (row.id)}
			<tr
				><td><Time value={row.recorded_at} /></td><td>{row.reason}</td><td><code>{JSON.stringify(row.fields)}</code></td
				></tr
			>
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
</Section>
