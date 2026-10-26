<script lang="ts">
	import { enhance } from '$app/forms';
	import { Plus } from '@lucide/svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import Check from '#lib/components/Check.svelte';
	import DeleteButton from '#lib/components/DeleteButton.svelte';
	import EditButton from '#lib/components/EditButton.svelte';
	import Field from '#lib/components/Field.svelte';
	import FilterForm from '#lib/components/FilterForm.svelte';
	import FormDialog from '#lib/components/FormDialog.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import Section from '#lib/components/Section.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import Table from '#lib/components/Table.svelte';
	import { Submission } from '#lib/enhance.svelte.js';
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

	const gone = new SvelteSet<string>();
	const rows = $derived(tables.filter((table) => !gone.has(table.name)));
	const create = new Submission();
	const update = new Submission({
		inline: true,
		onsuccess: () => {
			editing = null;
		}
	});
	const remove = new Submission({
		inline: true,
		begin: () => {
			const name = removing?.name ?? '';

			gone.add(name);

			return () => gone.delete(name);
		},
		onsuccess: () => {
			removing = null;
		}
	});
</script>

<Section title="Tables">
	<FilterForm
		action={paging.action}
		label="Filter the tables"
		params={paging.params}
		own={['table_schema', 'table_q']}
		pages={['tables_page']}
	>
		<label class="flex flex-col gap-1 text-sm">
			<span>Schema</span>
			<select class="select select-sm" name="table_schema" value={paging.params.get('table_schema') ?? ''}>
				<option value="">Any</option>
				{#each schemas as schema (schema.name)}<option value={schema.name}>{schema.name}</option>{/each}
			</select>
		</label>
		<Field label="Name contains" name="table_q" defaultValue={paging.params.get('table_q') ?? ''} />
	</FilterForm>
	<Table
		head={['Name', 'Schema', 'Strategy', 'Cache TTL', 'Sort', 'Synced', '']}
		caption="Tables"
		count={rows.length}
		empty="No table matches these filters."
	>
		{#each rows as table (table.name)}
			{@const row = syncedOf(table.name)}
			<tr>
				<td>{table.name}</td>
				<td>{table.schema}</td>
				<td>{table.strategy}{table.n ? `, n=${table.n}` : ''}{table.fallback ? ', fallback' : ''}</td>
				<td>{table.cache_ttl ?? ''}</td>
				<td>{table.sort?.join(', ') ?? ''}</td>
				<td title={row?.signature ?? ''}>
					{#if row?.synced}<span class="badge badge-soft badge-sm badge-success"
							>yes, {row.partition_count} partitions</span
						>{:else}<span class="badge badge-soft badge-sm badge-neutral">no</span>{/if}
				</td>
				<td class="whitespace-nowrap">
					{#if can.edit}<EditButton onclick={() => (editing = table)} />{/if}
					{#if can.delete}<DeleteButton onclick={() => (removing = table)} />{/if}
				</td>
			</tr>
		{/each}
	</Table>
	<Pager
		page={paging.page}
		shown={rows.length}
		total={paging.total}
		action={paging.action}
		params={paging.params}
		pageKey="tables_page"
		pageKeys={['tables_page']}
	/>

	{#if can.create}
		<form method="POST" action="?/createTable" use:enhance={create.submit} class="toolbar">
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
			<SubmitButton class="btn-primary btn-sm" pending={create.pending}><Plus size={14} /> Add table</SubmitButton>
		</form>
	{/if}
</Section>

<FormDialog
	open={editing !== null}
	title="Edit table {editing?.name ?? ''}"
	action="?/updateTable"
	submission={update}
	onclose={() => (editing = null)}
>
	{#if editing}
		<input type="hidden" name="name" value={editing.name} />
		<label class="flex flex-col gap-1 text-sm">
			<span>Strategy</span>
			<select class="select w-full" name="strategy" value={editing.strategy}>
				<option>full</option><option>partitioned</option>
			</select>
		</label>
		<Field label="n" name="n" type="number" min="1" placeholder="n" value={editing.n ?? ''} />
		<Field
			label="Cache TTL in seconds"
			name="cache_ttl"
			type="number"
			min="0"
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
	{/if}
</FormDialog>

<FormDialog
	open={removing !== null}
	title="Delete table {removing?.name ?? ''}"
	action="?/deleteTable"
	submission={remove}
	onclose={() => (removing = null)}
	confirm="Delete"
	danger
>
	{#if removing}
		<p>This deletes the view of the table, its data in DuckLake and S3, and its sync state. Nobody can undo it.</p>
		<input type="hidden" name="name" value={removing.name} />
	{/if}
</FormDialog>
