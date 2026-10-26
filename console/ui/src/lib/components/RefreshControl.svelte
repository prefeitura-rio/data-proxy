<script lang="ts">
	import { RefreshCw } from '@lucide/svelte';
	import { INTERVALS } from '#lib/poll.js';
	import type { poll } from '#lib/poll.svelte.js';
	import { shortTime } from '#lib/time.js';

	let { refresher }: { refresher: ReturnType<typeof poll> } = $props();

	let now = $state(Date.now());

	$effect(() => {
		const timer = setInterval(() => {
			now = Date.now();
		}, 1000);

		return () => {
			clearInterval(timer);
		};
	});
</script>

<div class="flex items-center gap-2 text-sm text-base-content/70">
	<span aria-live="off">Updated {shortTime(new Date(refresher.updated).toISOString(), now)}</span>
	<button
		class="btn btn-circle btn-ghost btn-sm"
		type="button"
		aria-label="Refresh now"
		onclick={() => void refresher.refresh()}
	>
		<RefreshCw size={16} class={refresher.refreshing ? 'animate-spin' : ''} />
	</button>
	<label class="flex items-center gap-1">
		<span class="sr-only">Refresh every</span>
		<select
			class="select select-sm"
			value={refresher.seconds}
			onchange={(event) => {
				refresher.seconds = Number(event.currentTarget.value);
			}}
		>
			{#each INTERVALS as seconds (seconds)}
				<option value={seconds}>{seconds === 0 ? 'Refresh off' : `Every ${seconds} s`}</option>
			{/each}
		</select>
	</label>
</div>
