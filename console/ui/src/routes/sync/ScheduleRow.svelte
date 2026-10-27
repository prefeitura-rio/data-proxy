<script lang="ts">
	import { enhance } from '$app/forms';
	import { Pause, Play } from '@lucide/svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import SubmitButton from '#lib/components/SubmitButton.svelte';
	import Time from '#lib/components/Time.svelte';
	import { Submission } from '#lib/enhance.svelte.js';
	import type { Schedule } from '#lib/types.js';
	import { waitForChange } from '#lib/waiting.svelte.js';
	import EditSchedule from './EditSchedule.svelte';

	let { schedule, canControl }: { schedule: Schedule; canControl: boolean } = $props();

	const change = waitForChange('app:sync', () => `${schedule.status} ${schedule.schedule}`);
	const control = new Submission(change.options);
</script>

<tr>
	<td>{schedule.schema}</td>
	<td>{schedule.schedule}</td>
	<td><StatusBadge status={schedule.status} /></td>
	<td><Time value={schedule.last_fired_at} /></td>
	{#if canControl}
		<td class="flex gap-1">
			<form method="POST" aria-label="Control the schedule {schedule.name}" use:enhance={control.submit}>
				<input type="hidden" name="name" value={schedule.name} />
				{#if schedule.status === 'PAUSED'}
					<SubmitButton class="btn-xs" pending={control.pending || change.waiting} formaction="?/resume"
						><Play size={12} /> Resume</SubmitButton
					>
				{:else}
					<SubmitButton class="btn-xs" pending={control.pending || change.waiting} formaction="?/pause"
						><Pause size={12} /> Pause</SubmitButton
					>
				{/if}
			</form>
			<EditSchedule {schedule} options={change.options} />
		</td>
	{/if}
</tr>
