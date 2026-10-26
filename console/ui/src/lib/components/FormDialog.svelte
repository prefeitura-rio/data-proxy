<script lang="ts">
	import { enhance } from '$app/forms';
	import type { Snippet } from 'svelte';
	import type { Submission } from '#lib/enhance.svelte.js';
	import Modal from './Modal.svelte';
	import SubmitButton from './SubmitButton.svelte';

	let {
		open,
		title,
		action,
		submission,
		onclose,
		confirm = 'Save',
		danger = false,
		disabled = false,
		children
	}: {
		open: boolean;
		title: string;
		action: string;
		submission: Submission;
		onclose: () => void;
		confirm?: string;
		danger?: boolean;
		disabled?: boolean;
		children: Snippet;
	} = $props();
</script>

<Modal {open} {onclose}>
	<h3 class="text-lg font-bold">{title}</h3>
	<form method="POST" {action} use:enhance={submission.submit} class="flex flex-col gap-2 py-2">
		{@render children()}
		{#if submission.error}
			<p role="alert" class="alert alert-soft alert-error text-base-content">{submission.error}</p>
		{/if}
		<div class="modal-action">
			<button class="btn" type="button" onclick={onclose}>Cancel</button>
			<SubmitButton class={danger ? 'btn-error' : 'btn-primary'} pending={submission.pending} {disabled}
				>{confirm}</SubmitButton
			>
		</div>
	</form>
</Modal>
