<script lang="ts">
	import { resolve } from '$app/paths';
	import { RUN_STATUSES } from '#lib/runs.js';
	import Field from '#lib/components/Field.svelte';
	import FilterForm from '#lib/components/FilterForm.svelte';

	let { params, schemas }: { params: { get(name: string): string | null; toString(): string }; schemas: string[] } =
		$props();
</script>

<FilterForm
	action={resolve('/sync')}
	label="Filter the runs"
	{params}
	own={['status', 'schema', 'from', 'to', 'id']}
	pages={['page']}
>
	<label class="flex flex-col gap-1 text-sm">
		<span>Status</span>
		<select class="select select-sm" name="status" value={params.get('status') ?? ''}>
			<option value="">Any</option>
			{#each RUN_STATUSES as status (status)}<option value={status}>{status}</option>{/each}
		</select>
	</label>
	<label class="flex flex-col gap-1 text-sm">
		<span>Schema</span>
		<select class="select select-sm" name="schema" value={params.get('schema') ?? ''}>
			<option value="">Any</option>
			{#each schemas as schema (schema)}<option value={schema}>{schema}</option>{/each}
		</select>
	</label>
	<Field label="From" name="from" type="date" defaultValue={params.get('from') ?? ''} />
	<Field label="To" name="to" type="date" defaultValue={params.get('to') ?? ''} />
	<Field label="Run id starts with" name="id" defaultValue={params.get('id') ?? ''} />
</FilterForm>
