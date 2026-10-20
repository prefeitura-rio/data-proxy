<script lang="ts">
	import * as v from 'valibot';
	import { Grant } from '#lib/types.js';
	import type { PageProps } from './$types';

	const Ready = v.object({ type: v.literal('ready') });

	let { data }: PageProps = $props();

	let schema = $state('');
	let chosen = $state<string[]>([]);
	let open = $state<{ schema: string; tables: string[] } | null>(null);
	let message = $state('');
	let frame = $state<HTMLIFrameElement>();

	const options = $derived(data.tables.filter((table) => table.schema === schema));
	const encrypted = $derived(data.schemas.find((row) => row.name === schema)?.encrypted ?? false);

	async function send(target: { schema: string; tables: string[] }) {
		const response = await fetch('/query/grant', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(target)
		});

		if (!response.ok) {
			const body = v.safeParse(v.object({ message: v.string() }), await response.json().catch(() => null));
			message =
				response.status === 409
					? 'The files of this schema are encrypted, so the notebook can not read them.'
					: body.success
						? body.output.message
						: 'The grant failed.';
			return;
		}

		message = '';
		const grant = v.parse(Grant, await response.json());
		frame?.contentWindow?.postMessage({ type: 'grant', grant }, data.notebookOrigin ?? '');
	}

	$effect(() => {
		const target = open;

		if (!target || !data.notebookOrigin) {
			return;
		}

		const listener = (event: MessageEvent) => {
			if (event.origin === data.notebookOrigin && event.source === frame?.contentWindow && v.is(Ready, event.data)) {
				void send(target);
			}
		};

		window.addEventListener('message', listener);
		const timer = setInterval(() => void send(target), 4 * 60 * 1000);

		return () => {
			window.removeEventListener('message', listener);
			clearInterval(timer);
		};
	});
</script>

<h1 class="mb-4 text-2xl font-semibold">Query</h1>

{#if !data.notebookOrigin}
	<p class="alert alert-warning">The query tab is not set up. Set the notebook origin, the catalog and the storage.</p>
{:else}
	<div role="alert" class="mb-4 alert alert-info">
		Row policies do not apply to files. The notebook reads every row of the tables that you open.
	</div>

	<form
		class="mb-4 flex flex-wrap items-end gap-4"
		onsubmit={(event) => {
			event.preventDefault();
			open = { schema, tables: chosen };
		}}
	>
		<label class="label gap-2">
			Schema
			<select class="select" bind:value={schema} onchange={() => (chosen = [])}>
				<option value="" disabled>Choose a schema</option>
				{#each data.schemas as row (row.name)}<option value={row.name}>{row.name}</option>{/each}
			</select>
		</label>
		<fieldset class="flex flex-wrap gap-3">
			{#each options as table (table.name)}
				<label class="label gap-1"
					><input
						type="checkbox"
						class="checkbox checkbox-sm"
						value={table.name}
						bind:group={chosen}
					/>{table.name}</label
				>
			{/each}
		</fieldset>
		<button class="btn btn-primary" type="submit" disabled={!schema || chosen.length === 0 || encrypted}
			>Open notebook</button
		>
	</form>

	{#if encrypted}<p class="text-warning">
			The files of this schema are encrypted, so the notebook can not read them.
		</p>{/if}
	{#if message}<p role="alert" class="text-error">{message}</p>{/if}

	{#if open}
		<iframe
			bind:this={frame}
			title="Notebook"
			src={data.notebookOrigin}
			sandbox="allow-scripts allow-same-origin allow-downloads allow-forms"
			class="h-[80vh] w-full rounded border"
		></iframe>
	{/if}
{/if}
