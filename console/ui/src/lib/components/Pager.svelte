<script lang="ts">
	import { paramsWith, rangeText, type PageQuery } from '#lib/list.js';

	let {
		page,
		shown,
		total,
		action,
		params,
		pageKey = 'page',
		pageKeys = ['page']
	}: {
		page: PageQuery;
		shown: number;
		total: number | null;
		action: string;
		params: { toString(): string };
		pageKey?: string;
		pageKeys?: string[];
	} = $props();

	const older = $derived(total === null ? shown === page.size : (page.page + 1) * page.size < total);
	const fields = (target: number) => [...paramsWith(params, { [pageKey]: String(target) }, pageKeys)];
</script>

<nav class="mt-2 flex items-center gap-2 text-sm" aria-label="Pages">
	<span data-testid="range">{rangeText(page, shown, total)}</span>
	{#if page.page > 0}
		<form method="GET" {action}>
			{#each fields(page.page - 1) as [name, value] (name)}<input type="hidden" {name} {value} />{/each}
			<button class="btn btn-sm" type="submit">Newer</button>
		</form>
	{/if}
	{#if older}
		<form method="GET" {action}>
			{#each fields(page.page + 1) as [name, value] (name)}<input type="hidden" {name} {value} />{/each}
			<button class="btn btn-sm" type="submit">Older</button>
		</form>
	{/if}
</nav>
