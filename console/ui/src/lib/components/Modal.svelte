<script lang="ts">
	import type { Snippet } from 'svelte';

	let { open, onclose, children }: { open: boolean; onclose: () => void; children: Snippet } = $props();

	function sync(dialog: HTMLDialogElement) {
		if (open && !dialog.open) {
			dialog.showModal();
		} else if (!open && dialog.open) {
			dialog.close();
		}
	}
</script>

<!-- A click on the backdrop is a click on the dialog itself, and a click in the box is not. -->
<dialog
	class="modal"
	{@attach sync}
	{onclose}
	onclick={(event) => {
		if (event.target === event.currentTarget) {
			onclose();
		}
	}}
>
	<div class="modal-box">{@render children()}</div>
</dialog>
