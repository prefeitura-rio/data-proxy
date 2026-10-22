<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		head,
		small = false,
		class: className = '',
		children
	}: { head: string[]; small?: boolean; class?: string; children: Snippet } = $props();
</script>

<div class={['overflow-x-auto rounded-box border border-base-300 bg-base-100', className]}>
	<table class={['table', small && 'table-sm']}>
		<thead class="bg-base-200/60 text-xs tracking-wide uppercase">
			<tr>
				{#each head as name (name)}<th>{name}</th>{/each}
			</tr>
		</thead>
		<tbody>{@render children()}</tbody>
	</table>
</div>
