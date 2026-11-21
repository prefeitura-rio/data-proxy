<script lang="ts">
	import { enhance } from '$app/forms';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { Plus } from '@lucide/svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import DeleteButton from '#lib/components/DeleteButton.svelte';
	import EditButton from '#lib/components/EditButton.svelte';
	import Field from '#lib/components/Field.svelte';
	import FilterForm from '#lib/components/FilterForm.svelte';
	import FormAlert from '#lib/components/FormAlert.svelte';
	import FormDialog from '#lib/components/FormDialog.svelte';
	import LogSection from './LogSection.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import Section from '#lib/components/Section.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import Table from '#lib/components/Table.svelte';
	import { Submission } from '#lib/enhance.svelte.js';
	import type { PolicyRow } from '#lib/types.js';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();

	let editing = $state<PolicyRow | null>(null);
	let removing = $state<PolicyRow | null>(null);
	const gone = new SvelteSet<string>();

	const keyOf = (row: PolicyRow) => `${row.scope}|${row.subject}|${row.unit_type}|${row.unit_id}`;
	const scopeLabel = (scope: string) => (scope === '' ? '*' : scope);
	const rows = $derived(data.policy.filter((row) => !gone.has(keyOf(row))));
	const add = new Submission();
	const update = new Submission({
		inline: true,
		onsuccess: () => {
			editing = null;
		}
	});
	const remove = new Submission({
		inline: true,
		begin: () => {
			const key = removing ? keyOf(removing) : '';

			gone.add(key);

			return () => gone.delete(key);
		},
		onsuccess: () => {
			removing = null;
		}
	});
</script>

<PageHeader title="Access">
	<form method="GET" action={resolve('/access')}>
		<label class="flex items-center gap-2 text-sm">
			<span class="text-base-content/70">Scope</span>
			<select class="select select-sm" name="scope" onchange={(event) => event.currentTarget.form?.requestSubmit()}>
				{#each data.scopes as name (name)}<option selected={name === data.scope}>{name}</option>{/each}
			</select>
		</label>
		<noscript><button class="btn btn-sm" type="submit">Show</button></noscript>
	</form>
</PageHeader>

<FormAlert {form} />

{#if data.scope}
	<Section title="Access policy">
		<p class="text-sm text-base-content/70">
			A grant covers its scope and what is below it. The scope * covers every project. A change takes effect
			immediately.
		</p>
		<FilterForm
			action={resolve('/access')}
			label="Search the policy"
			params={page.url.searchParams}
			own={['q']}
			pages={['page']}
			submit="Search"
		>
			<Field label="Subject or unit id contains" name="q" defaultValue={page.url.searchParams.get('q') ?? ''} />
		</FilterForm>
		<Table
			head={['Scope', 'Subject', 'Unit type', 'Unit id', '']}
			caption="Access policy"
			count={rows.length}
			empty="No policy matches this search."
		>
			{#each rows as row (keyOf(row))}
				<tr>
					<td>{scopeLabel(row.scope)}</td>
					<td>{row.subject}</td>
					<td>{row.unit_type ?? ''}</td>
					<td>{row.unit_id ?? ''}</td>
					<td class="whitespace-nowrap">
						{#if data.can.edit}<EditButton aria-label="Edit {row.subject}" onclick={() => (editing = row)} />{/if}
						{#if data.can.delete}<DeleteButton
								aria-label="Delete {row.subject}"
								onclick={() => (removing = row)}
							/>{/if}
					</td>
				</tr>
			{/each}
		</Table>
		<Pager
			page={data.policyPage}
			shown={rows.length}
			total={data.policyTotal}
			action={resolve('/access')}
			params={page.url.searchParams}
			pageKeys={['page']}
		/>
		{#if data.can.create}
			<form
				method="POST"
				action={`?/add&scope=${encodeURIComponent(data.scope)}`}
				use:enhance={add.submit}
				class="toolbar"
			>
				<label class="flex flex-col gap-1 text-sm">
					<span>Scope</span>
					<select class="select select-sm" name="scope">
						{#each data.scopes as name (name)}<option selected={name === data.scope}>{name}</option>{/each}
					</select>
				</label>
				<Field label="Subject" name="subject" placeholder="subject" required />
				<Field label="Unit type" name="unit_type" placeholder="unit type" required />
				<label class="flex min-w-64 flex-col gap-1 text-sm">
					<span class="text-base-content/70">Unit id</span>
					<textarea
						class="textarea field-sizing-content min-h-8 w-full resize-none py-[calc(0.25rem-1px)] leading-6 textarea-sm"
						name="unit_id"
						rows="1"
						placeholder="north, south"
						aria-describedby="unit-id-help"
						required></textarea>
				</label>
				<SubmitButton class="btn-primary btn-sm" pending={add.pending}><Plus size={14} /> Add</SubmitButton>
				<p id="unit-id-help" class="basis-full text-xs text-base-content/60">
					One id, or several separated by a comma or a new line. For every row use unit type * and unit id *.
				</p>
			</form>
		{/if}
	</Section>

	<LogSection log={data.log} total={data.logTotal} page={data.logPage} params={page.url.searchParams} />
{:else}
	<p class="text-base-content/70">No scope to show.</p>
{/if}

<FormDialog
	open={editing !== null}
	title="Edit access policy of {editing?.subject ?? ''}"
	action={`?/edit&scope=${encodeURIComponent(data.scope ?? '')}`}
	submission={update}
	onclose={() => (editing = null)}
>
	{#if editing}
		<input type="hidden" name="old_scope" value={editing.scope} />
		<input type="hidden" name="old_subject" value={editing.subject} />
		<input type="hidden" name="old_unit_type" value={editing.unit_type ?? ''} />
		<input type="hidden" name="old_unit_id" value={editing.unit_id ?? ''} />
		<Field label="Subject" name="subject" value={editing.subject} required />
		<Field label="Unit type" name="unit_type" value={editing.unit_type ?? ''} required />
		<Field label="Unit id" name="unit_id" value={editing.unit_id ?? ''} required />
	{/if}
</FormDialog>

<FormDialog
	open={removing !== null}
	title="Delete access policy of {removing?.subject ?? ''}"
	action={`?/remove&scope=${encodeURIComponent(data.scope ?? '')}`}
	submission={remove}
	onclose={() => (removing = null)}
	confirm="Delete"
	danger
>
	{#if removing}
		<p>
			{removing.subject} loses access to {removing.unit_type}:{removing.unit_id} in {scopeLabel(removing.scope)}. The
			change takes effect immediately.
		</p>
		<input type="hidden" name="old_scope" value={removing.scope} />
		<input type="hidden" name="old_subject" value={removing.subject} />
		<input type="hidden" name="old_unit_type" value={removing.unit_type ?? ''} />
		<input type="hidden" name="old_unit_id" value={removing.unit_id ?? ''} />
	{/if}
</FormDialog>
