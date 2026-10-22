<script lang="ts">
	let {
		onCancel,
		confirm = 'Save',
		danger = false,
		disabled = false
	}: {
		onCancel: () => void;
		confirm?: string;
		danger?: boolean;
		disabled?: boolean;
	} = $props();
</script>

<div class="modal-action">
	<button class="btn" type="button" onclick={onCancel}>Cancel</button>
	<button class={['btn', danger ? 'btn-error' : 'btn-primary']} type="submit" {disabled}>{confirm}</button>
</div>
