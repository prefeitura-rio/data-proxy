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

<dialog class="modal" {@attach sync} {onclose}>
	<div class="modal-box">{@render children()}</div>
</dialog>
