<script lang="ts">
	import { isActive, tone } from '#lib/status.js';

	let { status }: { status: string } = $props();
</script>

<span class={['badge badge-soft badge-sm', tone(status)]}>
	{#if isActive(status)}<span class="loading loading-xs loading-spinner" aria-hidden="true"></span>{/if}
	{status}
</span>
