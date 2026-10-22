<script lang="ts">
	import { enhance } from '$app/forms';
	import { refreshAll } from '$app/navigation';
	import { Pause, Play } from '@lucide/svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import Table from '#lib/components/Table.svelte';
	import type { Schedule } from '#lib/types.js';

	let {
		schedules,
		canControl,
		scheduled,
		problem
	}: { schedules: Schedule[]; canControl: boolean; scheduled?: string; problem?: string } = $props();

	let watching = $state(false);

	$effect(() => {
		if (!watching) {
			return;
		}

		const timer = setInterval(() => void refreshAll(), 2000);
		const stop = setTimeout(() => {
			watching = false;
		}, 20_000);

		return () => {
			clearInterval(timer);
			clearTimeout(stop);
		};
	});
</script>

<Table head={canControl ? ['Name', 'Cron', 'Status', ''] : ['Name', 'Cron', 'Status']} small>
	{#each schedules as schedule (schedule.name)}
		<tr>
			<td>{schedule.name}</td>
			<td>{schedule.schedule}</td>
			<td><StatusBadge status={schedule.status} /></td>
			{#if canControl}
				<td>
					<form
						method="POST"
						aria-label="Control the schedule {schedule.name}"
						use:enhance={() =>
							async ({ update }) => {
								await update();
								watching = true;
							}}
					>
						<input type="hidden" name="name" value={schedule.name} />
						{#if schedule.status === 'PAUSED'}
							<button class="btn btn-xs" type="submit" formaction="?/resume"><Play size={12} /> Resume</button>
						{:else}
							<button class="btn btn-xs" type="submit" formaction="?/pause"><Pause size={12} /> Pause</button>
						{/if}
					</form>
				</td>
			{/if}
		</tr>
	{/each}
</Table>
{#if scheduled}<p role="status" class="text-success">{scheduled}</p>{/if}
{#if problem}<p role="alert" class="text-error">{problem}</p>{/if}
