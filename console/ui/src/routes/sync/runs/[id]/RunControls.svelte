<script lang="ts">
	import { enhance } from '$app/forms';
	import { Ban, Play, RotateCcw } from '@lucide/svelte';
	import type { Controls } from '#lib/runs.js';

	let {
		controls,
		form,
		onsent
	}: { controls: Controls; form: { control?: string; message?: string } | null | undefined; onsent: () => void } =
		$props();
</script>

{#if controls.cancel || controls.resume || controls.retry}
	<form
		method="POST"
		aria-label="Control the run"
		class="mb-6 flex flex-wrap gap-2"
		use:enhance={() =>
			async ({ update }) => {
				await update();
				onsent();
			}}
	>
		{#if controls.cancel}
			<button class="btn btn-sm" type="submit" formaction="?/cancel"><Ban size={14} /> Cancel</button>
		{/if}
		{#if controls.resume}
			<button class="btn btn-sm" type="submit" formaction="?/resume"><Play size={14} /> Resume</button>
		{/if}
		{#if controls.retry}
			<button class="btn btn-sm" type="submit" formaction="?/retry"><RotateCcw size={14} /> Run again</button>
		{/if}
	</form>
{/if}
{#if form?.control}<p role="status" class="mb-4 alert alert-soft alert-success text-base-content">
		{form.control}
	</p>{/if}
{#if form?.message}<p role="alert" class="mb-4 alert alert-soft alert-error text-base-content">{form.message}</p>{/if}
