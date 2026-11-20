<script lang="ts">
	import { Trash } from '@lucide/svelte';
	import type { Snippet } from 'svelte';
	import type { Item } from '#lib/rules.js';

	let {
		entry,
		onremove,
		actions
	}: {
		entry: Item;
		onremove: () => void;
		/** The buttons that move the condition, before the delete button. */
		actions: Snippet;
	} = $props();
</script>

<div class="flex flex-wrap items-end gap-2 py-1">
	<label class="flex flex-col gap-1 text-xs">
		<span class="text-base-content/70">Column</span>
		<input class="input input-sm" name="column" bind:value={entry.column} placeholder="column" required />
	</label>
	<label class="flex flex-col gap-1 text-xs">
		<span class="text-base-content/70">Unit type</span>
		<input class="input input-sm" name="unit_type" bind:value={entry.unit_type} placeholder="unit type" required />
	</label>
	<div class="flex flex-wrap gap-1">
		{@render actions()}
		<button
			class="btn btn-ghost btn-xs"
			type="button"
			aria-label="Delete the condition {entry.column}"
			onclick={onremove}
		>
			<Trash size={14} /> Delete
		</button>
	</div>
</div>
