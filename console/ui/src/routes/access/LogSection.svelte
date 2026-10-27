<script lang="ts">
	import { resolve } from '$app/paths';
	import FilterForm from '#lib/components/FilterForm.svelte';
	import Field from '#lib/components/Field.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import Section from '#lib/components/Section.svelte';
	import Table from '#lib/components/Table.svelte';
	import Time from '#lib/components/Time.svelte';
	import type { PageQuery } from '#lib/list.js';
	import type { AccessLogRow } from '#lib/types.js';

	let {
		log,
		total,
		page: pageQuery,
		params
	}: {
		log: AccessLogRow[];
		total: number | null;
		page: PageQuery;
		params: { get(name: string): string | null; toString(): string };
	} = $props();
</script>

<Section title="Access log">
	<FilterForm
		action={resolve('/access')}
		label="Filter the access log"
		{params}
		own={['action', 'log_subject']}
		pages={['log_page']}
	>
		<label class="flex flex-col gap-1 text-sm">
			<span>Action</span>
			<select class="select select-sm" name="action" value={params.get('action') ?? ''}>
				<option value="">Any</option>
				<option value="insert">insert</option>
				<option value="update">update</option>
				<option value="delete">delete</option>
			</select>
		</label>
		<Field label="Subject contains" name="log_subject" defaultValue={params.get('log_subject') ?? ''} />
	</FilterForm>
	<Table
		head={['Time', 'Action', 'Subject', 'Unit']}
		small
		class="max-h-96 overflow-y-auto"
		caption="Access log"
		count={log.length}
		empty="No change matches these filters."
	>
		{#each log as row (`${row.changed_at}|${row.subject}|${row.action}|${row.unit_id}`)}
			<tr
				><td><Time value={row.changed_at} /></td><td>{row.action}</td><td>{row.subject}</td><td
					>{row.unit_type}:{row.unit_id}</td
				></tr
			>
		{/each}
	</Table>
	<Pager
		page={pageQuery}
		shown={log.length}
		{total}
		action={resolve('/access')}
		{params}
		pageKey="log_page"
		pageKeys={['log_page']}
	/>
</Section>
