<script lang="ts">
	import { invalidate } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { Download } from '@lucide/svelte';
	import FormAlert from '#lib/components/FormAlert.svelte';
	import SectionNav from '#lib/components/SectionNav.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import type { PageProps } from './$types';
	import HistorySection from './HistorySection.svelte';
	import ImportPicker from './ImportPicker.svelte';
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
				void invalidate('app:config');
			}
		}, 5000);

		return () => {
			clearInterval(timer);
		};
	});
</script>

<PageHeader title="Config">
	<a class="btn btn-sm" href={resolve('/config/export')} data-sveltekit-reload><Download size={16} /> Export</a>
	{#if data.can.import}<ImportPicker />{/if}
</PageHeader>

<SectionNav
	sections={[
		{ id: 'schemas', label: 'Schemas' },
		{ id: 'tables', label: 'Tables' },
		{ id: 'row-rules', label: 'Row rules' },
		{ id: 'history', label: 'History' }
	]}
/>

<FormAlert {form} />

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
