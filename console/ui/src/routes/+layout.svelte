<script lang="ts">
	import '../app.css';
	import favicon from '#lib/assets/favicon.svg';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { LogOut } from '@lucide/svelte';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>Data Proxy console</title>
</svelte:head>

<div class="navbar bg-base-200 px-4">
	<div class="flex-1 gap-4">
		<a class="text-lg font-semibold" href={resolve('/')}>Data Proxy</a>
		<div role="tablist" class="tabs tabs-border">
			{#each data.tabs as tab (tab.id)}
				<a role="tab" class="tab" class:tab-active={page.url.pathname.startsWith(tab.href)} href={resolve(tab.href)}
					>{tab.label}</a
				>
			{/each}
		</div>
	</div>
	{#if data.tabs.length > 0}
		{#if data.identity}
			<span class="text-sm" data-testid="identity"
				>{data.identity.name}{data.identity.role ? ` (${data.identity.role})` : ''}</span
			>
		{/if}
		<form method="POST" action="/auth/logout">
			<button class="btn btn-ghost btn-sm" type="submit"><LogOut size={16} /> Sign out</button>
		</form>
	{/if}
</div>

<main class="mx-auto max-w-6xl p-4">
	{@render children()}
</main>
