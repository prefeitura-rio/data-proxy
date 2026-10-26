<script lang="ts">
	import { fullTime, shortTime } from '#lib/time.js';

	let { value }: { value: string | null } = $props();

	/** The server renders the stored text. The browser then writes the time in the zone and language of the reader. */
	function localize(node: HTMLTimeElement) {
		if (value !== null) {
			node.textContent = shortTime(value, Date.now());
			node.title = fullTime(value);
		}
	}
</script>

{#if value !== null}<time datetime={value} {@attach localize}>{value}</time>{/if}
