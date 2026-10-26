<script lang="ts">
	import { navigating } from '$app/state';

	/** A load that ends in this time shows no bar, so a fast page doesn't flicker. */
	const DELAY_MS = 150;

	let visible = $state(false);

	$effect(() => {
		if (!navigating.to) {
			visible = false;
			return;
		}

		const timer = setTimeout(() => {
			visible = true;
		}, DELAY_MS);

		return () => {
			clearTimeout(timer);
		};
	});
</script>

{#if visible}
	<progress class="progress fixed inset-x-0 top-0 z-50 h-0.5 rounded-none progress-primary" aria-label="Loading"
	></progress>
{/if}
