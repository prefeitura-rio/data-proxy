<script lang="ts">
	import { resolve } from '$app/paths';
	import { RUN_STATUSES } from '#lib/runs.js';
	import Field from '#lib/components/Field.svelte';

	let { params, schemas }: { params: { get(name: string): string | null }; schemas: string[] } = $props();
</script>

<form method="GET" action={resolve('/sync')} class="toolbar" aria-label="Filter the runs">
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
	<Field label="From" name="from" type="date" value={params.get('from') ?? ''} />
	<Field label="To" name="to" type="date" value={params.get('to') ?? ''} />
	<Field label="Run id starts with" name="id" value={params.get('id') ?? ''} />
	<button class="btn btn-primary btn-sm" type="submit">Filter</button>
	<a class="btn btn-sm" href={resolve('/sync')}>Clear</a>
</form>
