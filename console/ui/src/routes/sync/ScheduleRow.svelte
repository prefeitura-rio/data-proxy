<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidate } from '$app/navigation';
	import { Pause, Play } from '@lucide/svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import { Submission } from '#lib/enhance.svelte.js';
	import type { Schedule } from '#lib/types.js';

	let { schedule, canControl }: { schedule: Schedule; canControl: boolean } = $props();

	/** The status before the click. The worker changes a schedule a moment later, so the row looks for the change. */
	let waitingFrom = $state<string | null>(null);

	let before = '';
	const control = new Submission({
		begin: () => {
			before = schedule.status;

			return () => undefined;
		},
		onupdated: () => {
			waitingFrom = before;
		}
	});

	$effect(() => {
		if (waitingFrom === null) {
			return;
		}

		if (schedule.status !== waitingFrom) {
			waitingFrom = null;
			return;
		}

		const timer = setInterval(() => void invalidate('app:sync'), 1000);
		const stop = setTimeout(() => {
			waitingFrom = null;
		}, 20_000);

		return () => {
			clearInterval(timer);
			clearTimeout(stop);
		};
	});
</script>

<tr>
	<td>{schedule.name}</td>
	<td>{schedule.schedule}</td>
	<td><StatusBadge status={schedule.status} /></td>
	{#if canControl}
		<td>
			<form method="POST" aria-label="Control the schedule {schedule.name}" use:enhance={control.submit}>
				<input type="hidden" name="name" value={schedule.name} />
				{#if schedule.status === 'PAUSED'}
					<SubmitButton class="btn-xs" pending={control.pending || waitingFrom !== null} formaction="?/resume"
						><Play size={12} /> Resume</SubmitButton
					>
				{:else}
					<SubmitButton class="btn-xs" pending={control.pending || waitingFrom !== null} formaction="?/pause"
						><Pause size={12} /> Pause</SubmitButton
					>
				{/if}
			</form>
		</td>
	{/if}
</tr>
