<script lang="ts">
	import { Pencil } from '@lucide/svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	let { ...rest }: HTMLButtonAttributes = $props();
</script>

<button class="btn btn-ghost btn-xs" type="button" {...rest}><Pencil size={14} /> Edit</button>
