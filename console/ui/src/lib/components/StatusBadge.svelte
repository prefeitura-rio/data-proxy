<script lang="ts">
	import { tone } from '#lib/status.js';

	let { status }: { status: string } = $props();
</script>

<span class={['badge badge-soft badge-sm', tone(status)]}>{status}</span>
