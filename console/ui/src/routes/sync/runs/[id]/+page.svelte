<script lang="ts">
	import type { PageProps } from './$types';
	import RunView from './RunView.svelte';

	let { data, form }: PageProps = $props();
</script>

{#key data.run.id}
	<RunView {data} {form} />
{/key}
