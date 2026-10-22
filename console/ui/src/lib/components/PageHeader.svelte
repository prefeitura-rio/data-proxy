<script lang="ts">
	import type { Snippet } from 'svelte';

	let { title, children }: { title: string; children?: Snippet } = $props();
</script>

<header class="mb-6 flex flex-wrap items-center justify-between gap-3">
	<h1 class="text-2xl font-semibold tracking-tight">{title}</h1>
	{#if children}<div class="flex flex-wrap items-center gap-2">{@render children()}</div>{/if}
</header>
