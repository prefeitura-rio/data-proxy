<script lang="ts">
	import Pager from '#lib/components/Pager.svelte';
	import Table from '#lib/components/Table.svelte';
	import type { PageQuery } from '#lib/list.js';
	import type { HistoryRow, SchemaRow } from '#lib/types.js';

	let {
		rows,
		schemas,
		paging
	}: {
		rows: HistoryRow[];
		schemas: SchemaRow[];
		paging: {
			page: PageQuery;
			total: number | null;
			action: string;
			params: { toString(): string; get(name: string): string | null };
		};
	} = $props();

	const keep = $derived(
		[...new URLSearchParams(paging.params.toString())].filter(
			([key]) => !['history_schema', 'history_object', 'history_page'].includes(key)
		)
	);
</script>

<h2 class="mt-6 mb-2 text-lg font-semibold">History</h2>
<form method="GET" action={paging.action} class="mb-2 flex items-end gap-2" aria-label="Filter the history">
	{#each keep as [key, value] (key)}
		<input type="hidden" name={key} {value} />
	{/each}
	<label class="flex flex-col gap-1 text-sm">
		<span>History of schema</span>
		<select class="select select-sm" name="history_schema" value={paging.params.get('history_schema') ?? ''}>
			<option value="">Any</option>
			{#each schemas as schema (schema.name)}<option value={schema.name}>{schema.name}</option>{/each}
		</select>
	</label>
	<label class="flex flex-col gap-1 text-sm">
		<span>Object</span>
		<select class="select select-sm" name="history_object" value={paging.params.get('history_object') ?? ''}>
			<option value="">Any</option>
			<option value="schemas">schemas</option>
			<option value="tables">tables</option>
			<option value="rls">rls</option>
		</select>
	</label>
	<button class="btn btn-primary btn-sm" type="submit">Filter</button>
</form>
<Table head={['Time', 'User', 'Action', 'Object', 'Schema', 'Key']} small>
	{#each rows as row (row.id)}
		<tr>
			<td>{row.changed_at}</td>
			<td>{row.changed_by ?? ''}</td>
			<td>{row.action}</td>
			<td>{row.object}</td>
			<td>{row.schema_name}</td>
			<td>{row.key}</td>
		</tr>
	{/each}
</Table>
<Pager
	page={paging.page}
	shown={rows.length}
	total={paging.total}
	action={paging.action}
	params={paging.params}
	pageKey="history_page"
	pageKeys={['history_page']}
/>
