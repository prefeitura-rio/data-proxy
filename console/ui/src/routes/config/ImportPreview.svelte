<script lang="ts">
	import { enhance } from '$app/forms';
	import { Check } from '@lucide/svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import Table from '#lib/components/Table.svelte';
	import { Submission } from '#lib/enhance.svelte.js';
	import { actionTone, summaryText } from '#lib/preview.js';
	import type { ImportChange } from '#lib/types.js';

	let { changes, documentText }: { changes: ImportChange[]; documentText: string } = $props();

	const apply = new Submission();
	const visible = $derived(changes.filter((change) => change.action !== 'unchanged'));
</script>

<section class="card mb-8 bg-base-100 card-border" aria-label="Import preview">
	<div class="card-body gap-3">
		<h2 class="card-title">Import preview</h2>
		<p data-testid="preview-summary">{summaryText(changes)}</p>
		{#if visible.length > 0}
			<Table head={['Action', 'Object', 'Schema', 'Key']} small>
				{#each visible as change (`${change.object}|${change.key}|${change.action}`)}
					<tr
						><td><span class={['badge badge-soft badge-sm', actionTone(change.action)]}>{change.action}</span></td><td
							>{change.object}</td
						><td>{change.schema_name}</td><td>{change.key}</td></tr
					>
				{/each}
			</Table>
		{/if}
		<form method="POST" action="?/import" use:enhance={apply.submit}>
			<input type="hidden" name="document_text" value={documentText} />
			<SubmitButton class="btn-primary btn-sm" pending={apply.pending}><Check size={16} /> Apply import</SubmitButton>
		</form>
	</div>
</section>
