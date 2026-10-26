<script lang="ts">
	import '../app.css';
	import favicon from '#lib/assets/favicon.svg';
	import { resolve } from '$app/paths';
	import { navigating, page } from '$app/state';
	import { Database, LogOut } from '@lucide/svelte';
	import NavProgress from '#lib/components/NavProgress.svelte';
	import ThemeToggle from '#lib/components/ThemeToggle.svelte';
	import Toaster from '#lib/components/Toaster.svelte';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();

	/** The page runs its scripts from now on. The browser tests wait for this attribute before they type or click. */
	$effect(() => {
		document.documentElement.dataset.hydrated = 'true';
	});
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<a
	class="sr-only focus:btn focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:btn-primary focus:btn-sm"
	href="#main">Skip to content</a
>
<NavProgress />
<Toaster />

<header class="sticky top-0 z-10 border-b border-base-300 bg-base-100/90 backdrop-blur">
	<div class="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-1 px-4 py-2">
		<a class="flex items-center gap-2 text-lg font-semibold tracking-tight whitespace-nowrap" href={resolve('/')}>
			<Database size={20} class="text-primary" /> Data Proxy
		</a>
		<nav aria-label="Main" class="tabs tabs-border order-last w-full overflow-x-auto sm:order-none sm:w-auto">
			{#each data.tabs as tab (tab.id)}
				{@const current = page.url.pathname.startsWith(resolve(tab.href))}
				<a
					class={['tab', current && 'tab-active']}
					aria-current={current ? 'page' : undefined}
					data-sveltekit-preload-code="eager"
					href={resolve(tab.href)}>{tab.label}</a
				>
			{/each}
		</nav>
		<div class="ml-auto flex items-center gap-2">
			<ThemeToggle />
			{#if data.tabs.length > 0}
				{#if data.identity}
					<span class="badge hidden badge-soft sm:inline-flex" data-testid="identity"
						>{data.identity.name}{data.identity.role ? ` (${data.identity.role})` : ''}</span
					>
				{/if}
				<form method="POST" action={resolve('/auth/logout')}>
					<button class="btn btn-ghost btn-sm" type="submit"><LogOut size={16} /> Sign out</button>
				</form>
			{/if}
		</div>
	</div>
</header>

<main
	id="main"
	tabindex="-1"
	class={['mx-auto max-w-6xl px-4 py-8 transition-opacity outline-none', navigating.to && 'opacity-70']}
	aria-busy={navigating.to !== null}
>
	{@render children()}
</main>
