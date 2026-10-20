<script lang="ts">
	let { form }: { form: { message?: string | null; saved?: boolean } | null | undefined } = $props();
</script>

{#if form?.message}<p role="alert" class="mb-4 alert alert-error">{form.message}</p>{/if}
{#if form?.saved}<p role="status" class="mb-4 alert alert-success">Saved.</p>{/if}
