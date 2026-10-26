<script lang="ts">
	import { CircleAlert } from '@lucide/svelte';

	let { form }: { form: { message?: string | null; toast?: string } | null | undefined } = $props();
</script>

{#if form?.message}
	<p role="alert" class="mb-4 alert alert-soft alert-error text-base-content">
		<CircleAlert size={18} />{form.message}
	</p>
{/if}
<!-- A browser without JavaScript gets no toast, so the page says what happened. -->
<noscript
	>{#if form?.toast}<p class="mb-4 alert alert-soft alert-success text-base-content">{form.toast}</p>{/if}</noscript
>
