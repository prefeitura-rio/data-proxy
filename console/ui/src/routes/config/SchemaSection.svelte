<script lang="ts">
	import { enhance, type SubmitFunction } from '$app/forms';
	import { Plus } from '@lucide/svelte';
	import Check from '#lib/components/Check.svelte';
	import DeleteButton from '#lib/components/DeleteButton.svelte';
	import DialogActions from '#lib/components/DialogActions.svelte';
	import EditButton from '#lib/components/EditButton.svelte';
	import Field from '#lib/components/Field.svelte';
	import Modal from '#lib/components/Modal.svelte';
	import Section from '#lib/components/Section.svelte';
	import Table from '#lib/components/Table.svelte';
	import type { Can } from '#lib/permissions.js';
	import type { Health, SchemaRow } from '#lib/types.js';

	let { schemas, health, can }: { schemas: SchemaRow[]; health: Health[]; can: Can } = $props();

	let editing = $state<SchemaRow | null>(null);
	let removing = $state<SchemaRow | null>(null);
	let typed = $state('');

	const statusOf = (name: string) => {
		const row = health.find((item) => item.name === name);

		return row?.schema_exists && row.access_policy ? `ready, ${row.view_count} views` : 'provisioning';
	};

	const close: SubmitFunction =
		() =>
		async ({ update }) => {
			editing = null;
			removing = null;
			await update();
		};
</script>

<Section title="Schemas">
	<Table head={['Name', 'Source', 'Claim', 'Encrypted', 'Health', '']}>
		{#each schemas as schema (schema.name)}
			<tr>
				<td>{schema.name}</td>
				<td>{schema.source}</td>
				<td>{schema.claim ?? ''}</td>
				<td>
					{#if schema.encrypted}<span class="badge badge-soft badge-sm badge-warning">yes</span>{:else}no{/if}
				</td>
				<td>
					<span
						class={[
							'badge badge-soft badge-sm',
							statusOf(schema.name) === 'provisioning' ? 'badge-warning' : 'badge-success'
						]}>{statusOf(schema.name)}</span
					>
				</td>
				<td class="whitespace-nowrap">
					{#if can.edit}<EditButton onclick={() => (editing = schema)} />{/if}
					{#if can.delete}
						<DeleteButton
							onclick={() => {
								typed = '';
								removing = schema;
							}}
						/>
					{/if}
				</td>
			</tr>
		{/each}
	</Table>

	{#if can.create}
		<form method="POST" action="?/createSchema" use:enhance class="toolbar">
			<Field label="Name" name="name" placeholder="name" required />
			<Field label="Source" name="source" placeholder="source (bigquery)" />
			<Field label="Claim" name="claim" placeholder="claim" />
			<Check label="encrypted" name="encrypted" />
			<button class="btn btn-primary btn-sm" type="submit"><Plus size={14} /> Add schema</button>
		</form>
	{/if}
</Section>

<Modal open={editing !== null} onclose={() => (editing = null)}>
	{#if editing}
		<h3 class="text-lg font-bold">Edit schema {editing.name}</h3>
		<form method="POST" action="?/updateSchema" use:enhance={close} class="flex flex-col gap-2 py-2">
			<input type="hidden" name="name" value={editing.name} />
			<Field label="Claim" name="claim" placeholder="claim" value={editing.claim ?? ''} />
			<Check label="encrypted" name="encrypted" checked={editing.encrypted} />
			<DialogActions onCancel={() => (editing = null)} />
		</form>
	{/if}
</Modal>

<Modal open={removing !== null} onclose={() => (removing = null)}>
	{#if removing}
		<h3 class="text-lg font-bold">Delete schema {removing.name}</h3>
		<p class="py-2">
			This deletes the data files of the schema in S3, its schema in the catalog, and its schema in the database. Nobody
			can undo it. Type the name of the schema to confirm.
		</p>
		<form method="POST" action="?/deleteSchema" use:enhance={close}>
			<input type="hidden" name="name" value={removing.name} />
			<input
				class="input w-full"
				name="confirm"
				bind:value={typed}
				autocomplete="off"
				aria-label="Name of the schema"
			/>
			<DialogActions onCancel={() => (removing = null)} confirm="Delete" danger disabled={typed !== removing.name} />
		</form>
	{/if}
</Modal>
