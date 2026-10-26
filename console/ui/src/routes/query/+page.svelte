<script lang="ts">
	import { NotebookText, RotateCw } from '@lucide/svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import { notebookBridge } from '#lib/notebook.svelte.js';
	import { destination } from '#lib/tables.js';
	import type { PageProps } from './$types';

	const SCHEMA_KEY = 'query-schema';

	let { data }: PageProps = $props();

	let schema = $state('');
	let chosen = $state<string[]>([]);
	let open = $state<{ schema: string; tables: string[] } | null>(null);
	let frame = $state<HTMLIFrameElement>();
	let attempt = $state(0);

	/** The notebook writes one cell for each table in the address. */
	const address = $derived(
		`${data.notebookOrigin ?? ''}/?${(open?.tables ?? []).map((table) => `tables=${encodeURIComponent(destination(table))}`).join('&')}`
	);
	const options = $derived(data.tables.filter((table) => table.schema === schema));
	const encrypted = $derived(data.schemas.find((row) => row.name === schema)?.encrypted ?? false);
	const hint = $derived(schema === '' ? 'Choose a schema.' : chosen.length === 0 ? 'Choose at least one table.' : '');
	const bridge = notebookBridge({
		origin: () => data.notebookOrigin,
		ttlSeconds: () => data.grantTtlSeconds,
		frame: () => frame,
		target: () => open,
		attempt: () => attempt
	});

	/** The reader gets the schema of the last visit back. */
	$effect(() => {
		try {
			const stored = localStorage.getItem(SCHEMA_KEY);

			if (stored !== null && data.schemas.some((row) => row.name === stored)) {
				schema = stored;
			}
		} catch {
			schema = '';
		}
	});

	function remember() {
		chosen = [];

		try {
			localStorage.setItem(SCHEMA_KEY, schema);
		} catch {
			return;
		}
	}
</script>

<PageHeader title="Query" />

{#if !data.notebookOrigin}
	<p class="alert alert-soft alert-warning text-base-content">
		The query tab is not set up. Set the notebook origin, the catalog and the storage.
	</p>
{:else}
	<div role="alert" class="mb-4 alert alert-soft alert-info text-base-content">
		Row policies do not apply to files. The notebook reads every row of the tables that you open.
	</div>

	<form
		class="mb-4 toolbar gap-4"
		onsubmit={(event) => {
			event.preventDefault();
			open = { schema, tables: chosen };
		}}
	>
		<label class="flex flex-col gap-1 text-sm">
			<span class="text-base-content/70">Schema</span>
			<select class="select select-sm" bind:value={schema} onchange={remember}>
				<option value="" disabled>Choose a schema</option>
				{#each data.schemas as row (row.name)}<option value={row.name}>{row.name}</option>{/each}
			</select>
		</label>
		<fieldset class="flex flex-wrap items-center gap-3 pb-1">
			{#if options.length > 0}
				<button
					class="btn btn-ghost btn-xs"
					type="button"
					onclick={() => {
						chosen = chosen.length === options.length ? [] : options.map((table) => table.name);
					}}>{chosen.length === options.length ? 'Clear' : 'Select all'}</button
				>
			{/if}
			{#each options as table (table.name)}
				<label class="flex cursor-pointer items-center gap-2 text-sm"
					><input
						type="checkbox"
						class="checkbox checkbox-sm"
						value={table.name}
						bind:group={chosen}
					/>{table.name}</label
				>
			{/each}
		</fieldset>
		<button
			class="btn btn-primary btn-sm"
			type="submit"
			disabled={!schema || chosen.length === 0 || encrypted}
			aria-describedby="query-hint"><NotebookText size={16} /> Open notebook</button
		>
	</form>

	<p id="query-hint" class="mb-4 text-sm text-base-content/70">{hint}</p>

	{#if encrypted}<p class="text-warning">
			The files of this schema are encrypted, so the notebook can not read them.
		</p>{/if}
	{#if bridge.message}<p role="alert" class="text-error">{bridge.message}</p>{/if}

	{#if open}
		<div class="relative">
			{#key attempt}
				<iframe
					bind:this={frame}
					title="Notebook"
					src={address}
					sandbox="allow-scripts allow-same-origin allow-downloads allow-forms"
					class="h-[80vh] w-full rounded-box border border-base-300 bg-base-100 shadow-sm"
				></iframe>
			{/key}
			{#if !bridge.ready}
				<div
					class="absolute inset-0 flex flex-col items-center justify-center gap-3 rounded-box bg-base-100/80 backdrop-blur-sm"
				>
					{#if bridge.timedOut}
						<p role="alert" class="text-error">The notebook did not answer.</p>
						<button
							class="btn btn-sm"
							type="button"
							onclick={() => {
								attempt += 1;
							}}><RotateCw size={14} /> Reload</button
						>
					{:else}
						<span class="loading loading-md loading-spinner" aria-hidden="true"></span>
						<p class="text-sm text-base-content/70">Starting the notebook…</p>
					{/if}
				</div>
			{/if}
		</div>
	{/if}
{/if}
