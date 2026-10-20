<script lang="ts">
	import { enhance } from '$app/forms';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { Plus, Save, Trash } from '@lucide/svelte';
	import Field from '#lib/components/Field.svelte';
	import FormAlert from '#lib/components/FormAlert.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import Table from '#lib/components/Table.svelte';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();
</script>

<h1 class="mb-4 text-2xl font-semibold">Access</h1>

<form method="GET" action={resolve('/access')}>
	<label class="label mb-4 gap-2">
		Schema
		<select class="select" name="schema" onchange={(event) => event.currentTarget.form?.requestSubmit()}>
			{#each data.schemas as name (name)}<option selected={name === data.schema}>{name}</option>{/each}
		</select>
	</label>
</form>

<FormAlert {form} />

{#if data.schema}
	<p class="mb-2">A change takes effect at once.</p>
	<form method="GET" action={resolve('/access')} class="mb-2 flex items-end gap-2" aria-label="Search the policy">
		<input type="hidden" name="schema" value={data.schema} />
		<Field label="Subject or unit id contains" name="q" value={page.url.searchParams.get('q') ?? ''} />
		<button class="btn btn-primary btn-sm" type="submit">Search</button>
	</form>
	<Table head={['Subject', 'Unit type', 'Unit id', '']}>
		{#each data.policy as row (`${row.subject}|${row.unit_type}|${row.unit_id}`)}
			<tr>
				<td colspan="4">
					<form method="POST" action={`?/edit&schema=${data.schema}`} use:enhance class="flex gap-2">
						<input type="hidden" name="old_subject" value={row.subject} />
						<input type="hidden" name="old_unit_type" value={row.unit_type ?? ''} />
						<input type="hidden" name="old_unit_id" value={row.unit_id ?? ''} />
						<input
							class="input input-sm"
							name="subject"
							aria-label="Subject of {row.subject}"
							value={row.subject}
							disabled={!data.can.edit}
						/>
						<input
							class="input input-sm"
							name="unit_type"
							aria-label="Unit type of {row.subject}"
							value={row.unit_type ?? ''}
							disabled={!data.can.edit}
						/>
						<input
							class="input input-sm"
							name="unit_id"
							aria-label="Unit id of {row.subject}"
							value={row.unit_id ?? ''}
							disabled={!data.can.edit}
						/>
						{#if data.can.edit}<button class="btn btn-sm" type="submit"><Save size={14} /> Save</button>{/if}
						{#if data.can.delete}
							<button class="btn btn-ghost btn-sm" type="submit" formaction={`?/remove&schema=${data.schema}`}
								><Trash size={14} /> Delete</button
							>
						{/if}
					</form>
				</td>
			</tr>
		{/each}
	</Table>
	<Pager
		page={data.policyPage}
		shown={data.policy.length}
		total={data.policyTotal}
		action={resolve('/access')}
		params={page.url.searchParams}
		pageKeys={['page']}
	/>
	{#if data.can.create}
		<form method="POST" action={`?/add&schema=${data.schema}`} use:enhance class="my-2 flex items-end gap-2">
			<Field label="Subject" name="subject" placeholder="subject" required />
			<Field label="Unit type" name="unit_type" placeholder="unit type" required />
			<Field label="Unit id" name="unit_id" placeholder="unit id" required />
			<button class="btn btn-primary btn-sm" type="submit"><Plus size={14} /> Add</button>
		</form>
	{/if}

	<h2 class="mt-6 mb-2 text-lg font-semibold">Access log</h2>
	<form method="GET" action={resolve('/access')} class="mb-2 flex items-end gap-2" aria-label="Filter the access log">
		<input type="hidden" name="schema" value={data.schema} />
		<label class="flex flex-col gap-1 text-sm">
			<span>Action</span>
			<select class="select select-sm" name="action" value={page.url.searchParams.get('action') ?? ''}>
				<option value="">Any</option>
				<option value="insert">insert</option>
				<option value="update">update</option>
				<option value="delete">delete</option>
			</select>
		</label>
		<Field label="Subject contains" name="log_subject" value={page.url.searchParams.get('log_subject') ?? ''} />
		<button class="btn btn-primary btn-sm" type="submit">Filter</button>
	</form>
	<Table head={['Time', 'Action', 'Subject', 'Unit']} small class="max-h-96 overflow-y-auto">
		{#each data.log as row (`${row.changed_at}|${row.subject}|${row.action}|${row.unit_id}`)}
			<tr><td>{row.changed_at}</td><td>{row.action}</td><td>{row.subject}</td><td>{row.unit_type}:{row.unit_id}</td></tr
			>
		{/each}
	</Table>
	<Pager
		page={data.logPage}
		shown={data.log.length}
		total={data.logTotal}
		action={resolve('/access')}
		params={page.url.searchParams}
		pageKey="log_page"
		pageKeys={['log_page']}
	/>
{:else}
	<p>No schema to show.</p>
{/if}
