<script lang="ts">
	import '../app.css';
	import favicon from '#lib/assets/favicon.svg';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { Database, LogOut } from '@lucide/svelte';
	import ThemeToggle from '#lib/components/ThemeToggle.svelte';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>Data Proxy console</title>
</svelte:head>

<div class="sticky top-0 z-10 border-b border-base-300 bg-base-100/90 backdrop-blur">
	<div class="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-1 px-4 py-2">
		<a class="flex items-center gap-2 text-lg font-semibold tracking-tight whitespace-nowrap" href={resolve('/')}>
			<Database size={20} class="text-primary" /> Data Proxy
		</a>
		<div role="tablist" class="tabs tabs-border order-last w-full overflow-x-auto sm:order-none sm:w-auto">
			{#each data.tabs as tab (tab.id)}
				<a role="tab" class="tab" class:tab-active={page.url.pathname.startsWith(tab.href)} href={resolve(tab.href)}
					>{tab.label}</a
				>
			{/each}
		</div>
		<div class="ml-auto flex items-center gap-2">
			<ThemeToggle />
			{#if data.tabs.length > 0}
				{#if data.identity}
					<span class="badge hidden badge-soft sm:inline-flex" data-testid="identity"
						>{data.identity.name}{data.identity.role ? ` (${data.identity.role})` : ''}</span
					>
				{/if}
				<form method="POST" action="/auth/logout">
					<button class="btn btn-ghost btn-sm" type="submit"><LogOut size={16} /> Sign out</button>
				</form>
			{/if}
		</div>
	</div>
</div>

<main class="mx-auto max-w-6xl px-4 py-8">
	{@render children()}
</main>
