<script lang="ts">
	import { Pencil } from '@lucide/svelte';
	import Section from '#lib/components/Section.svelte';
	import { Submission } from '#lib/enhance.svelte.js';
	import type { Can } from '#lib/permissions.js';
	import { describe, toEditor } from '#lib/rules.js';
	import type { RulesRow } from '#lib/types.js';
	import RuleEditor from './RuleEditor.svelte';

	let { rules, tables, can }: { rules: RulesRow[]; tables: { name: string; schema: string }[]; can: Can } = $props();

	let editing = $state<string | null>(null);

	const save = new Submission({
		inline: true,
		onsuccess: () => {
			editing = null;
		}
	});

	const byTable = $derived(
		tables.map((table) => {
			const own = rules.find((rule) => rule.table_name === table.name)?.groups ?? [];

			return { name: table.name, own, sentence: describe(toEditor(own)) };
		})
	);
	const shown = $derived(byTable.filter((table) => table.own.length > 0 || can.create));
</script>

<Section title="Row rules">
	{#if byTable.every((table) => table.own.length === 0)}
		<p class="text-sm text-base-content/60">No row rule yet.</p>
	{/if}
	<ul class="flex flex-col gap-2">
		{#each shown as table (table.name)}
			<li class="rounded-box border border-base-300 bg-base-100 p-3">
				<div class="flex flex-wrap items-center justify-between gap-2">
					<h3 class="text-sm font-semibold break-all">{table.name}</h3>
					{#if can.edit || can.create}
						<button
							class="btn btn-ghost btn-xs"
							type="button"
							aria-label="Edit the row rules of {table.name}"
							onclick={() => (editing = table.name)}
						>
							<Pencil size={14} /> Edit
						</button>
					{/if}
				</div>
				<p class="pt-1 text-sm text-base-content/70">{table.sentence}</p>
			</li>
		{/each}
	</ul>
</Section>

<RuleEditor
	table={editing}
	schema={tables.find((table) => table.name === editing)?.schema ?? ''}
	stored={rules.find((rule) => rule.table_name === editing)?.groups ?? []}
	submission={save}
	onclose={() => (editing = null)}
/>
