<script lang="ts">
	import { CircleAlert, CircleCheck } from '@lucide/svelte';

	let { form }: { form: { message?: string | null; saved?: boolean } | null | undefined } = $props();
</script>

{#if form?.message}
	<p role="alert" class="mb-4 alert alert-soft alert-error text-base-content">
		<CircleAlert size={18} />{form.message}
	</p>
{/if}
{#if form?.saved}
	<p role="status" class="mb-4 alert alert-soft alert-success text-base-content"><CircleCheck size={18} />Saved.</p>
{/if}
