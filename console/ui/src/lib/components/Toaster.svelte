<script lang="ts">
	import { CircleAlert, CircleCheck, X } from '@lucide/svelte';
	import { resolve } from '$app/paths';
	import { fly } from 'svelte/transition';
	import { dismiss, toasts } from '#lib/toast.svelte.js';
</script>

<div role="status" aria-live="polite" class="toast toast-end toast-bottom z-50">
	{#each toasts as toast (toast.id)}
		<div
			class={['alert shadow-md', toast.kind === 'error' ? 'alert-error' : 'alert-success']}
			transition:fly={{ y: 16, duration: 150 }}
		>
			{#if toast.kind === 'error'}<CircleAlert size={18} />{:else}<CircleCheck size={18} />{/if}
			<span>{toast.text}</span>
			{#if toast.run}<a class="link font-medium" href={resolve('/sync/runs/[id]', { id: toast.run })}>Open run</a>{/if}
			<button
				class="btn btn-circle btn-ghost btn-xs"
				type="button"
				aria-label="Dismiss"
				onclick={() => {
					dismiss(toast.id);
				}}
			>
				<X size={14} />
			</button>
		</div>
	{/each}
</div>
