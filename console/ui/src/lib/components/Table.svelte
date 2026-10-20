<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		head,
		small = false,
		class: className = '',
		children
	}: { head: string[]; small?: boolean; class?: string; children: Snippet } = $props();
</script>

<div class={['overflow-x-auto', className]}>
	<table class={['table', small && 'table-sm']}>
		<thead>
			<tr>
				{#each head as name (name)}<th>{name}</th>{/each}
			</tr>
		</thead>
		<tbody>{@render children()}</tbody>
	</table>
</div>
