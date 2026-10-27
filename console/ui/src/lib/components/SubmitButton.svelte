<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	let {
		pending = false,
		disabled = false,
		class: className = 'btn-primary',
		children,
		...rest
	}: { pending?: boolean; disabled?: boolean; children: Snippet } & Omit<HTMLButtonAttributes, 'disabled'> = $props();
</script>

<button class={['btn', className]} type="submit" disabled={disabled || pending} aria-busy={pending} {...rest}>
	{#if pending}<span class="loading loading-xs loading-spinner" aria-hidden="true"></span>{/if}
	{@render children()}
</button>
