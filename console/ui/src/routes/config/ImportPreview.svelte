<script lang="ts">
	import { enhance } from '$app/forms';
	import { Check } from '@lucide/svelte';
	import Table from '#lib/components/Table.svelte';
	import { summaryText } from '#lib/preview.js';
	import type { ImportChange } from '#lib/types.js';

	let { changes, documentText }: { changes: ImportChange[]; documentText: string } = $props();

	const visible = $derived(changes.filter((change) => change.action !== 'unchanged'));
</script>

<section class="card mb-8 bg-base-100 card-border" aria-label="Import preview">
	<div class="card-body gap-3">
		<h2 class="card-title">Import preview</h2>
		<p data-testid="preview-summary">{summaryText(changes)}</p>
		{#if visible.length > 0}
			<Table head={['Action', 'Object', 'Schema', 'Key']} small>
				{#each visible as change (`${change.object}|${change.key}|${change.action}`)}
					<tr><td>{change.action}</td><td>{change.object}</td><td>{change.schema_name}</td><td>{change.key}</td></tr>
				{/each}
			</Table>
		{/if}
		<form method="POST" action="?/import" use:enhance>
			<input type="hidden" name="document_text" value={documentText} />
			<button class="btn btn-primary btn-sm" type="submit"><Check size={16} /> Apply import</button>
		</form>
	</div>
</section>
