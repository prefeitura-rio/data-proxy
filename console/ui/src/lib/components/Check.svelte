<script lang="ts">
	import type { HTMLInputAttributes } from 'svelte/elements';

	let { label, ...rest }: { label: string } & HTMLInputAttributes = $props();
</script>

<label class="flex cursor-pointer items-center gap-2 text-sm">
	<input type="checkbox" class="checkbox checkbox-sm" {...rest} />
	{label}
</label>
