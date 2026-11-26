<script lang="ts">
	import { failedTasksLabel } from '#lib/runs.js';
	import StatusBadge from './StatusBadge.svelte';

	let { status, failed }: { status: string; failed: number } = $props();

	const label = $derived(failedTasksLabel(failed));
</script>

<StatusBadge {status} />
{#if label !== null}<span class="badge badge-soft badge-sm badge-warning">{label}</span>{/if}
