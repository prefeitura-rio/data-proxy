<script lang="ts">
	import { isActive } from '#lib/status.js';
	import { duration } from '#lib/time.js';
	import type { Run, Task } from '#lib/types.js';

	let {
		run,
		status,
		tasks,
		total,
		now
	}: { run: Run; status: string; tasks: Task[]; total: number | null; now: number } = $props();

	const known = $derived(total !== null && total === tasks.length);
	const finished = $derived(tasks.filter((task) => task.completed_at !== null).length);
	const began = $derived(run.started_at ?? run.created_at);
	const end = $derived(run.completed_at === null ? (isActive(status) ? now : null) : Date.parse(run.completed_at));
</script>

<div class="mb-6 flex flex-col gap-2 text-sm">
	{#if known && tasks.length > 0}
		<progress class="progress w-full max-w-md progress-primary" value={finished} max={tasks.length}></progress>
	{/if}
	<p class="text-base-content/70">
		{run.task_count}
		{run.task_count === 1 ? 'task' : 'tasks'}{known && tasks.length > 0 ? `, ${finished} finished` : ''}, {run.failed_task_count}
		failed{began !== null && end !== null ? `. It ran for ${duration(end - Date.parse(began))}.` : '.'}
	</p>
</div>
