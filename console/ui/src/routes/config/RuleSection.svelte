<script lang="ts">
	import { enhance, type SubmitFunction } from '$app/forms';
	import { Plus } from '@lucide/svelte';
	import DeleteButton from '#lib/components/DeleteButton.svelte';
	import DialogActions from '#lib/components/DialogActions.svelte';
	import EditButton from '#lib/components/EditButton.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Table from '#lib/components/Table.svelte';
	import type { Can } from '#lib/permissions.js';
	import type { RlsRow } from '#lib/types.js';

	let { rules, tables, can }: { rules: RlsRow[]; tables: { name: string }[]; can: Can } = $props();

	let editing = $state<RlsRow | null>(null);

	const close: SubmitFunction =
		() =>
		async ({ update }) => {
			editing = null;
			await update();
		};
</script>

<h2 class="mt-6 mb-2 text-lg font-semibold">Row rules</h2>
<Table head={['Table', 'Column', 'Unit type', '']}>
	{#each rules as rule (rule.id)}
		<tr>
			<td>{rule.table_name}</td>
			<td>{rule.column_name}</td>
			<td>{rule.unit_type}</td>
			<td class="whitespace-nowrap">
				{#if can.edit}<EditButton onclick={() => (editing = rule)} />{/if}
				{#if can.delete}
					<form method="POST" action="?/deleteRls" use:enhance class="inline">
						<input type="hidden" name="id" value={rule.id} />
						<DeleteButton type="submit" />
					</form>
				{/if}
			</td>
		</tr>
	{/each}
</Table>

{#if can.create}
	<form method="POST" action="?/createRls" use:enhance class="my-2 flex flex-wrap items-end gap-2">
		<label class="flex flex-col gap-1 text-sm">
			<span>Table</span>
			<select class="select select-sm" name="table_name" required>
				{#each tables as table (table.name)}<option>{table.name}</option>{/each}
			</select>
		</label>
		<Field label="Column" name="column_name" placeholder="column" required />
		<Field label="Unit type" name="unit_type" placeholder="unit type" required />
		<button class="btn btn-primary btn-sm" type="submit"><Plus size={14} /> Add rule</button>
	</form>
{/if}

<Modal open={editing !== null} onclose={() => (editing = null)}>
	{#if editing}
		<h3 class="text-lg font-bold">Edit row rule of {editing.table_name}</h3>
		<form method="POST" action="?/updateRls" use:enhance={close} class="flex flex-col gap-2 py-2">
			<input type="hidden" name="id" value={editing.id} />
			<Field label="Column" name="column_name" placeholder="column" value={editing.column_name} required />
			<Field label="Unit type" name="unit_type" placeholder="unit type" value={editing.unit_type} required />
			<DialogActions onCancel={() => (editing = null)} />
		</form>
	{/if}
</Modal>
