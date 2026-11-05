<script lang="ts">
	import { enhance } from '$app/forms';
	import { Plus } from '@lucide/svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import DeleteButton from '#lib/components/DeleteButton.svelte';
	import Field from '#lib/components/Field.svelte';
	import FormDialog from '#lib/components/FormDialog.svelte';
	import Section from '#lib/components/Section.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import Table from '#lib/components/Table.svelte';
	import { Submission } from '#lib/enhance.svelte.js';
	import type { Can } from '#lib/permissions.js';
	import { DEFAULT_PROJECT } from '#lib/projects.js';
	import type { ProjectRow, SchemaRow } from '#lib/types.js';

	let { projects, schemas, can }: { projects: ProjectRow[]; schemas: SchemaRow[]; can: Can } = $props();

	let removing = $state<ProjectRow | null>(null);
	const gone = new SvelteSet<string>();

	const rows = $derived(projects.filter((project) => !gone.has(project.name)));
	const countOf = (name: string) => schemas.filter((schema) => schema.project === name).length;
	const create = new Submission();
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

<Section title="Projects">
	<Table head={['Name', 'Schemas', '']} caption="Projects" count={rows.length} empty="No project yet.">
		{#each rows as project (project.name)}
			<tr>
				<td>{project.name}</td>
				<td>{countOf(project.name)}</td>
				<td class="whitespace-nowrap">
					{#if can.delete && project.name !== DEFAULT_PROJECT && countOf(project.name) === 0}
						<DeleteButton onclick={() => (removing = project)} />
					{/if}
				</td>
			</tr>
		{/each}
	</Table>

	{#if can.create}
		<form method="POST" action="?/createProject" use:enhance={create.submit} class="toolbar">
			<Field label="Name" name="name" placeholder="letters and digits" pattern="[a-z][a-z0-9]*" required />
			<SubmitButton class="btn-primary btn-sm" pending={create.pending}><Plus size={14} /> Add project</SubmitButton>
		</form>
	{/if}
</Section>

<FormDialog
	open={removing !== null}
	title="Delete project {removing?.name ?? ''}"
	action="?/deleteProject"
	submission={remove}
	onclose={() => (removing = null)}
	confirm="Delete"
	danger
>
	{#if removing}
		<p>The project has no schema. This deletes only the project.</p>
		<input type="hidden" name="name" value={removing.name} />
	{/if}
</FormDialog>
