<script lang="ts">
	import type { HTMLInputAttributes } from 'svelte/elements';

	let { label, ...rest }: { label: string } & HTMLInputAttributes = $props();
</script>

<label class="flex flex-col gap-1 text-sm">
	<span>{label}</span>
	<input class="input w-full input-sm" {...rest} />
</label>
