<script lang="ts">
	import { Trash } from '@lucide/svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	let { ...rest }: HTMLButtonAttributes = $props();
</script>

<button class="btn btn-ghost text-error btn-xs" type="button" {...rest}><Trash size={14} /> Delete</button>
