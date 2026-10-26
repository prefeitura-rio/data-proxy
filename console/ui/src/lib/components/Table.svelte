<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		head,
		small = false,
		class: className = '',
		caption,
		count,
		empty = 'No rows.',
		children
	}: {
		head: string[];
		small?: boolean;
		class?: string;
		/** The name of the table for a screen reader. */
		caption?: string;
		/** The number of rows. A table with none says `empty` below the head. */
		count?: number;
		empty?: string;
		children: Snippet;
	} = $props();
</script>

<div class={['overflow-x-auto rounded-box border border-base-300 bg-base-100', className]}>
	<table class={['table', small && 'table-sm']}>
		{#if caption}<caption class="sr-only">{caption}</caption>{/if}
		<thead class="bg-base-200/60 text-xs tracking-wide uppercase">
			<tr>
				{#each head as name (name)}<th scope="col">{name}</th>{/each}
			</tr>
		</thead>
		<tbody>{@render children()}</tbody>
	</table>
	{#if count === 0}<p class="px-4 py-6 text-center text-sm text-base-content/60">{empty}</p>{/if}
</div>
