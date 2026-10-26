<script lang="ts">
	import { enhance } from '$app/forms';
	import { Plus } from '@lucide/svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import DeleteButton from '#lib/components/DeleteButton.svelte';
	import EditButton from '#lib/components/EditButton.svelte';
	import Field from '#lib/components/Field.svelte';
	import FormDialog from '#lib/components/FormDialog.svelte';
	import Section from '#lib/components/Section.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import Table from '#lib/components/Table.svelte';
	import { Submission } from '#lib/enhance.svelte.js';
	import type { Can } from '#lib/permissions.js';
	import type { RlsRow } from '#lib/types.js';

	let { rules, tables, can }: { rules: RlsRow[]; tables: { name: string }[]; can: Can } = $props();

	let editing = $state<RlsRow | null>(null);
	let removing = $state<RlsRow | null>(null);
	const gone = new SvelteSet<number>();

	const rows = $derived(rules.filter((rule) => !gone.has(rule.id)));
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
			const id = removing?.id ?? 0;

			gone.add(id);

			return () => gone.delete(id);
		},
		onsuccess: () => {
			removing = null;
		}
	});
</script>

<Section title="Row rules">
	<Table head={['Table', 'Column', 'Unit type', '']} caption="Row rules" count={rows.length} empty="No row rule yet.">
		{#each rows as rule (rule.id)}
			<tr>
				<td>{rule.table_name}</td>
				<td>{rule.column_name}</td>
				<td>{rule.unit_type}</td>
				<td class="whitespace-nowrap">
					{#if can.edit}<EditButton onclick={() => (editing = rule)} />{/if}
					{#if can.delete}<DeleteButton onclick={() => (removing = rule)} />{/if}
				</td>
			</tr>
		{/each}
	</Table>

	{#if can.create}
		<form method="POST" action="?/createRls" use:enhance={create.submit} class="toolbar">
			<label class="flex flex-col gap-1 text-sm">
				<span>Table</span>
				<select class="select select-sm" name="table_name" required>
					{#each tables as table (table.name)}<option>{table.name}</option>{/each}
				</select>
			</label>
			<Field label="Column" name="column_name" placeholder="column" required />
			<Field label="Unit type" name="unit_type" placeholder="unit type" required />
			<SubmitButton class="btn-primary btn-sm" pending={create.pending}><Plus size={14} /> Add rule</SubmitButton>
		</form>
	{/if}
</Section>

<FormDialog
	open={editing !== null}
	title="Edit row rule of {editing?.table_name ?? ''}"
	action="?/updateRls"
	submission={update}
	onclose={() => (editing = null)}
>
	{#if editing}
		<input type="hidden" name="id" value={editing.id} />
		<Field label="Column" name="column_name" placeholder="column" value={editing.column_name} required />
		<Field label="Unit type" name="unit_type" placeholder="unit type" value={editing.unit_type} required />
	{/if}
</FormDialog>

<FormDialog
	open={removing !== null}
	title="Delete row rule of {removing?.table_name ?? ''}"
	action="?/deleteRls"
	submission={remove}
	onclose={() => (removing = null)}
	confirm="Delete"
	danger
>
	{#if removing}
		<p>The rule stops filtering the rows of {removing.table_name} by {removing.column_name}. Nobody can undo it.</p>
		<input type="hidden" name="id" value={removing.id} />
	{/if}
</FormDialog>
