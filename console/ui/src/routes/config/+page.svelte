<script lang="ts">
	import { enhance } from '$app/forms';
	import { refreshAll } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { Download, Upload } from '@lucide/svelte';
	import FormAlert from '#lib/components/FormAlert.svelte';
	import type { PageProps } from './$types';
	import HistorySection from './HistorySection.svelte';
	import ImportPreview from './ImportPreview.svelte';
	import RuleSection from './RuleSection.svelte';
	import SchemaSection from './SchemaSection.svelte';
	import TableSection from './TableSection.svelte';

	let { data, form }: PageProps = $props();

	const pending = $derived(data.health.some((row) => !row.schema_exists || !row.access_policy));

	$effect(() => {
		if (!pending) {
			return;
		}

		const stop = Date.now() + 120_000;
		const timer = setInterval(() => {
			if (Date.now() > stop) {
				clearInterval(timer);
			} else {
				void refreshAll();
			}
		}, 5000);

		return () => {
			clearInterval(timer);
		};
	});
</script>

<h1 class="mb-4 text-2xl font-semibold">Config</h1>

<FormAlert {form} />

<div class="mb-4 flex gap-2">
	<a class="btn btn-sm" href={resolve('/config/export')} data-sveltekit-reload><Download size={16} /> Export</a>
	{#if data.can.import}
		<form method="POST" action="?/preview" enctype="multipart/form-data" use:enhance class="flex gap-2">
			<input
				class="file-input file-input-sm"
				type="file"
				name="document"
				accept="application/json"
				aria-label="Document to import"
			/>
			<button class="btn btn-sm" type="submit"><Upload size={16} /> Preview import</button>
		</form>
	{/if}
</div>

{#if form?.preview}
	<ImportPreview changes={form.preview} documentText={form.documentText} />
{/if}

<SchemaSection schemas={data.schemas} health={data.health} can={data.can} />
<TableSection
	tables={data.tables}
	schemas={data.schemas}
	synced={data.synced}
	can={data.can}
	paging={{
		page: data.tablesPage,
		total: data.tablesTotal,
		action: resolve('/config'),
		params: page.url.searchParams
	}}
/>
<RuleSection rules={data.rls} tables={data.tableNames} can={data.can} />
<HistorySection
	rows={data.history}
	schemas={data.schemas}
	paging={{
		page: data.historyPage,
		total: data.historyTotal,
		action: resolve('/config'),
		params: page.url.searchParams
	}}
/>
