<script lang="ts">
	import { enhance, type SubmitFunction } from '$app/forms';
	import { Plus } from '@lucide/svelte';
	import Check from '#lib/components/Check.svelte';
	import DeleteButton from '#lib/components/DeleteButton.svelte';
	import DialogActions from '#lib/components/DialogActions.svelte';
	import EditButton from '#lib/components/EditButton.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import Table from '#lib/components/Table.svelte';
	import type { PageQuery } from '#lib/list.js';
	import type { Can } from '#lib/permissions.js';
	import type { SchemaRow, Synced, TableRow } from '#lib/types.js';

	let {
		tables,
		schemas,
		synced,
		can,
		paging
	}: {
		tables: TableRow[];
		schemas: SchemaRow[];
		synced: Synced[];
		can: Can;
		paging: {
			page: PageQuery;
			total: number | null;
			action: string;
			params: { toString(): string; get(name: string): string | null };
		};
	} = $props();

	let editing = $state<TableRow | null>(null);
	let removing = $state<TableRow | null>(null);

	const syncedOf = (name: string) => synced.find((row) => row.name === name);

	const close: SubmitFunction =
		() =>
		async ({ update }) => {
			editing = null;
			removing = null;
			await update();
		};
</script>

<h2 class="mt-6 mb-2 text-lg font-semibold">Tables</h2>
<form method="GET" action={paging.action} class="mb-2 flex items-end gap-2" aria-label="Filter the tables">
	{#each [...new URLSearchParams(paging.params.toString())].filter(([key]) => !['table_schema', 'table_q', 'tables_page'].includes(key)) as [key, value] (key)}
		<input type="hidden" name={key} {value} />
	{/each}
	<label class="flex flex-col gap-1 text-sm">
		<span>Schema</span>
		<select class="select select-sm" name="table_schema" value={paging.params.get('table_schema') ?? ''}>
			<option value="">Any</option>
			{#each schemas as schema (schema.name)}<option value={schema.name}>{schema.name}</option>{/each}
		</select>
	</label>
	<Field label="Name contains" name="table_q" value={paging.params.get('table_q') ?? ''} />
	<button class="btn btn-primary btn-sm" type="submit">Filter</button>
</form>
<Table head={['Name', 'Schema', 'Strategy', 'Cache TTL', 'Sort', 'Synced', '']}>
	{#each tables as table (table.name)}
		{@const row = syncedOf(table.name)}
		<tr>
			<td>{table.name}</td>
			<td>{table.schema}</td>
			<td>{table.strategy}{table.n ? `, n=${table.n}` : ''}{table.fallback ? ', fallback' : ''}</td>
			<td>{table.cache_ttl ?? ''}</td>
			<td>{table.sort?.join(', ') ?? ''}</td>
			<td title={row?.signature ?? ''}>{row?.synced ? `yes, ${row.partition_count} partitions` : 'no'}</td>
			<td class="whitespace-nowrap">
				{#if can.edit}<EditButton onclick={() => (editing = table)} />{/if}
				{#if can.delete}<DeleteButton onclick={() => (removing = table)} />{/if}
			</td>
		</tr>
	{/each}
</Table>
<Pager
	page={paging.page}
	shown={tables.length}
	total={paging.total}
	action={paging.action}
	params={paging.params}
	pageKey="tables_page"
	pageKeys={['tables_page']}
/>

{#if can.create}
	<form method="POST" action="?/createTable" use:enhance class="my-2 flex flex-wrap items-end gap-2">
		<Field label="Table" name="name" placeholder="project.dataset.table" required />
		<label class="flex flex-col gap-1 text-sm">
			<span>Schema</span>
			<select class="select select-sm" name="schema" required>
				{#each schemas as schema (schema.name)}<option>{schema.name}</option>{/each}
			</select>
		</label>
		<label class="flex flex-col gap-1 text-sm">
			<span>Strategy</span>
			<select class="select select-sm" name="strategy"><option>full</option><option>partitioned</option></select>
		</label>
		<Check label="fallback" name="fallback" />
		<button class="btn btn-primary btn-sm" type="submit"><Plus size={14} /> Add table</button>
	</form>
{/if}

<Modal open={editing !== null} onclose={() => (editing = null)}>
	{#if editing}
		<h3 class="text-lg font-bold">Edit table {editing.name}</h3>
		<form method="POST" action="?/updateTable" use:enhance={close} class="flex flex-col gap-2 py-2">
			<input type="hidden" name="name" value={editing.name} />
			<label class="flex flex-col gap-1 text-sm">
				<span>Strategy</span>
				<select class="select w-full" name="strategy" value={editing.strategy}>
					<option>full</option><option>partitioned</option>
				</select>
			</label>
			<Field label="n" name="n" placeholder="n" value={editing.n ?? ''} />
			<Field
				label="Cache TTL in seconds"
				name="cache_ttl"
				placeholder="cache TTL in seconds"
				value={editing.cache_ttl ?? ''}
			/>
			<Field
				label="Sort columns, separated by commas"
				name="sort"
				placeholder="sort columns, separated by commas"
				value={editing.sort?.join(', ') ?? ''}
			/>
			<Check label="fallback" name="fallback" checked={editing.fallback} />
			<DialogActions onCancel={() => (editing = null)} />
		</form>
	{/if}
</Modal>

<Modal open={removing !== null} onclose={() => (removing = null)}>
	{#if removing}
		<h3 class="text-lg font-bold">Delete table {removing.name}</h3>
		<p class="py-2">
			This deletes the view of the table, its data in DuckLake and S3, and its sync state. Nobody can undo it.
		</p>
		<form method="POST" action="?/deleteTable" use:enhance={close}>
			<input type="hidden" name="name" value={removing.name} />
			<DialogActions onCancel={() => (removing = null)} confirm="Delete" danger />
		</form>
	{/if}
</Modal>
