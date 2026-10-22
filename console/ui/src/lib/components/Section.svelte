<script lang="ts">
	import type { Snippet } from 'svelte';

	let { title, children }: { title: string; children: Snippet } = $props();
</script>

<section class="mb-8">
	<h2 class="mb-3 text-lg font-semibold">{title}</h2>
	<div class="flex flex-col gap-3">{@render children()}</div>
</section>
