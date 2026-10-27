<script lang="ts">
	import { enhance } from '$app/forms';
	import { Upload } from '@lucide/svelte';
	import { Submission } from '#lib/enhance.svelte.js';

	const preview = new Submission();

	let form = $state<HTMLFormElement>();
	let input = $state<HTMLInputElement>();
	let dragging = $state(false);

	function drop(event: DragEvent) {
		event.preventDefault();
		dragging = false;

		if (input && event.dataTransfer?.files.length) {
			input.files = event.dataTransfer.files;
			form?.requestSubmit();
		}
	}
</script>

<form
	bind:this={form}
	method="POST"
	action="?/preview"
	enctype="multipart/form-data"
	use:enhance={preview.submit}
	class="flex items-center gap-2"
>
	<!-- A drop has no keyboard path of its own. The file input inside the label is the keyboard path. -->
	<label
		class={['btn btn-sm', dragging && 'btn-primary']}
		ondragover={(event) => {
			event.preventDefault();
			dragging = true;
		}}
		ondragleave={() => {
			dragging = false;
		}}
		ondrop={drop}
	>
		{#if preview.pending}
			<span class="loading loading-xs loading-spinner" aria-hidden="true"></span>
		{:else}
			<Upload size={16} />
		{/if}
		{dragging ? 'Drop the file' : 'Import file'}
		<input
			bind:this={input}
			class="sr-only"
			type="file"
			name="document"
			accept="application/json"
			aria-label="Document to import"
			onchange={() => form?.requestSubmit()}
		/>
	</label>
	<noscript><button class="btn btn-sm" type="submit">Preview import</button></noscript>
</form>
