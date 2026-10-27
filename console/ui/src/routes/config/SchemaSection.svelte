<script lang="ts">
	import { enhance } from '$app/forms';
	import { Plus } from '@lucide/svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import Check from '#lib/components/Check.svelte';
	import DeleteButton from '#lib/components/DeleteButton.svelte';
	import EditButton from '#lib/components/EditButton.svelte';
	import Field from '#lib/components/Field.svelte';
	import FormDialog from '#lib/components/FormDialog.svelte';
	import Section from '#lib/components/Section.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import Table from '#lib/components/Table.svelte';
	import { Submission } from '#lib/enhance.svelte.js';
	import type { Can } from '#lib/permissions.js';
	import type { Health, SchemaRow } from '#lib/types.js';

	let { schemas, health, can }: { schemas: SchemaRow[]; health: Health[]; can: Can } = $props();

	let editing = $state<SchemaRow | null>(null);
	let removing = $state<SchemaRow | null>(null);
	let typed = $state('');
	const gone = new SvelteSet<string>();

	const rows = $derived(schemas.filter((schema) => !gone.has(schema.name)));
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

	const statusOf = (name: string) => {
		const row = health.find((item) => item.name === name);

		return row?.schema_exists && row.access_policy ? `ready, ${row.view_count} views` : 'provisioning';
	};
</script>

<Section title="Schemas">
	<Table
		head={['Name', 'Source', 'Claim', 'Encrypted', 'Health', '']}
		caption="Schemas"
		count={rows.length}
		empty="No schema yet."
	>
		{#each rows as schema (schema.name)}
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
						]}
					>
						{#if statusOf(schema.name) === 'provisioning'}<span
								class="loading loading-xs loading-spinner"
								aria-hidden="true"
							></span>{/if}
						{statusOf(schema.name)}
					</span>
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
		<form method="POST" action="?/createSchema" use:enhance={create.submit} class="toolbar">
			<Field label="Name" name="name" placeholder="name" required />
			<Field label="Source" name="source" placeholder="source (bigquery)" />
			<Field label="Claim" name="claim" placeholder="claim" />
			<Check label="encrypted" name="encrypted" />
			<SubmitButton class="btn-primary btn-sm" pending={create.pending}><Plus size={14} /> Add schema</SubmitButton>
		</form>
	{/if}
</Section>

<FormDialog
	open={editing !== null}
	title="Edit schema {editing?.name ?? ''}"
	action="?/updateSchema"
	submission={update}
	onclose={() => (editing = null)}
>
	{#if editing}
		<input type="hidden" name="name" value={editing.name} />
		<Field label="Claim" name="claim" placeholder="claim" value={editing.claim ?? ''} />
		<Check label="encrypted" name="encrypted" checked={editing.encrypted} />
	{/if}
</FormDialog>

<FormDialog
	open={removing !== null}
	title="Delete schema {removing?.name ?? ''}"
	action="?/deleteSchema"
	submission={remove}
	onclose={() => (removing = null)}
	confirm="Delete"
	danger
	disabled={typed !== removing?.name}
>
	{#if removing}
		<p>
			This deletes the data files of the schema in S3, its schema in the catalog, and its schema in the database. Nobody
			can undo it. Type the name of the schema to confirm.
		</p>
		<input type="hidden" name="name" value={removing.name} />
		<input class="input w-full" name="confirm" bind:value={typed} autocomplete="off" aria-label="Name of the schema" />
	{/if}
</FormDialog>
