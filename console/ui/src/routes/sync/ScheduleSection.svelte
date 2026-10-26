<script lang="ts">
	import Table from '#lib/components/Table.svelte';
	import type { Schedule } from '#lib/types.js';
	import ScheduleRow from './ScheduleRow.svelte';

	let { schedules, canControl, problem }: { schedules: Schedule[]; canControl: boolean; problem?: string } = $props();
</script>

<Table
	head={canControl ? ['Name', 'Cron', 'Status', ''] : ['Name', 'Cron', 'Status']}
	small
	caption="Schedules"
	count={schedules.length}
	empty="No schedule."
>
	{#each schedules as schedule (schedule.name)}
		<ScheduleRow {schedule} {canControl} />
	{/each}
</Table>
{#if problem}<p role="alert" class="text-error">{problem}</p>{/if}
