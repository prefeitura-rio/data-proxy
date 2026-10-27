<script lang="ts">
	import { enhance } from '$app/forms';
	import { Ban, Play, RotateCcw } from '@lucide/svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import { Submission } from '#lib/enhance.svelte.js';
	import type { Controls } from '#lib/runs.js';

	let {
		controls,
		form,
		onsent
	}: { controls: Controls; form: { message?: string } | null | undefined; onsent: () => void } = $props();

	const control = new Submission({
		onupdated: () => {
			onsent();
		}
	});
</script>

{#if controls.cancel || controls.resume || controls.retry}
	<form method="POST" aria-label="Control the run" class="mb-6 flex flex-wrap gap-2" use:enhance={control.submit}>
		{#if controls.cancel}
			<SubmitButton class="btn-sm" pending={control.pending} formaction="?/cancel"
				><Ban size={14} /> Cancel</SubmitButton
			>
		{/if}
		{#if controls.resume}
			<SubmitButton class="btn-sm" pending={control.pending} formaction="?/resume"
				><Play size={14} /> Resume</SubmitButton
			>
		{/if}
		{#if controls.retry}
			<SubmitButton class="btn-sm" pending={control.pending} formaction="?/retry"
				><RotateCcw size={14} /> Run again</SubmitButton
			>
		{/if}
	</form>
{/if}
{#if form?.message}<p role="alert" class="mb-4 alert alert-soft alert-error text-base-content">{form.message}</p>{/if}
