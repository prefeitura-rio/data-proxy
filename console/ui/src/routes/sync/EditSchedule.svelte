<script lang="ts">
	import { Pencil } from '@lucide/svelte';
	import Field from '#lib/components/Field.svelte';
	import FormDialog from '#lib/components/FormDialog.svelte';
	import { isCron } from '#lib/cron.js';
	import { Submission, type SubmissionOptions } from '#lib/enhance.svelte.js';
	import type { Schedule } from '#lib/types.js';

	let { schedule, options }: { schedule: Schedule; options: SubmissionOptions } = $props();

	let editing = $state(false);
	let cron = $state('');

	const submission = new Submission({
		inline: true,
		begin: () => options.begin?.() ?? (() => undefined),
		onsuccess: () => (editing = false),
		onupdated: () => options.onupdated?.()
	});
</script>

<button
	class="btn btn-xs"
	type="button"
	onclick={() => {
		cron = schedule.schedule;
		editing = true;
	}}><Pencil size={12} /> Edit</button
>
<FormDialog
	open={editing}
	title="Schedule of {schedule.schema}"
	action="?/set"
	{submission}
	onclose={() => (editing = false)}
	disabled={!isCron(cron.trim())}
>
	<input type="hidden" name="name" value={schedule.name} />
	<Field
		label="Cron"
		name="cron"
		value={cron}
		oninput={(event: Event & { currentTarget: HTMLInputElement }) => (cron = event.currentTarget.value)}
		required
	/>
	<p class="text-sm text-base-content/70">
		Five fields (minute, hour, day, month, weekday), or six with the seconds first. The times are UTC.
	</p>
</FormDialog>
