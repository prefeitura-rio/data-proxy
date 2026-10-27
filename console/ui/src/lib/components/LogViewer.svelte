<script lang="ts">
	import { ArrowDownToLine, Copy, Download } from '@lucide/svelte';
	import { atBottom, filterLogs, levelsOf, levelTone, lineParts, logText, tablesOf } from '#lib/logs.js';
	import { success } from '#lib/toast.svelte.js';
	import type { LogLine } from '#lib/types.js';

	let { lines, following, name }: { lines: LogLine[]; following: boolean; name: string } = $props();

	let level = $state('');
	let table = $state('');
	let text = $state('');
	let wrap = $state(false);
	let box = $state<HTMLPreElement>();
	let pinned = $state(true);

	const shown = $derived(filterLogs(lines, { level, table, text }));
	const levels = $derived(levelsOf(lines));
	const tables = $derived(tablesOf(lines));

	/** A log that still grows keeps its end in view, until the reader scrolls up. */
	$effect(() => {
		if (following && pinned && box && shown.length > 0) {
			box.scrollTop = box.scrollHeight;
		}
	});

	function jump() {
		pinned = true;

		if (box) {
			box.scrollTop = box.scrollHeight;
		}
	}

	async function copy() {
		await navigator.clipboard.writeText(logText(shown));
		success('Log copied.');
	}

	function download() {
		const link = document.createElement('a');

		link.href = URL.createObjectURL(new Blob([logText(shown)], { type: 'text/plain' }));
		link.download = `${name}.log`;
		link.click();
		URL.revokeObjectURL(link.href);
	}
</script>

<div class="flex flex-wrap items-end gap-3 text-sm">
	<label class="flex flex-col gap-1">
		<span class="text-base-content/70">Level</span>
		<select class="select select-sm" bind:value={level}>
			<option value="">Any</option>
			{#each levels as option (option)}<option>{option}</option>{/each}
		</select>
	</label>
	{#if tables.length > 0}
		<label class="flex flex-col gap-1">
			<span class="text-base-content/70">Table</span>
			<select class="select select-sm" bind:value={table}>
				<option value="">Any</option>
				{#each tables as option (option)}<option>{option}</option>{/each}
			</select>
		</label>
	{/if}
	<label class="flex flex-col gap-1">
		<span class="text-base-content/70">Message contains</span>
		<input class="input input-sm" type="search" bind:value={text} />
	</label>
	<label class="flex cursor-pointer items-center gap-2 pb-1">
		<input type="checkbox" class="checkbox checkbox-sm" bind:checked={wrap} /> Wrap lines
	</label>
	<div class="ml-auto flex gap-2">
		<button class="btn btn-sm" type="button" onclick={copy}><Copy size={14} /> Copy</button>
		<button class="btn btn-sm" type="button" onclick={download}><Download size={14} /> Download</button>
	</div>
</div>

<div class="relative">
	<!-- A box that scrolls needs the keyboard focus, so a reader without a mouse can scroll it. -->
	<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
	<pre
		bind:this={box}
		role="log"
		tabindex="0"
		aria-label="Log of the run"
		onscroll={() => {
			if (box) {
				pinned = atBottom(box);
			}
		}}
		class={[
			'max-h-96 overflow-auto rounded-box border border-base-300 bg-base-200 p-3 font-mono text-xs leading-relaxed'
		]}>{#each shown as line (`${line.workflow_id}:${line.position}`)}{@const parts = lineParts(line)}<span
				class={['block', wrap ? 'break-words whitespace-normal' : 'whitespace-nowrap']}
				>{parts.time} <span class={levelTone(line.level)}>{parts.level}</span> {#if parts.context !== ''}<span
						class="text-primary">{parts.context}</span
					>
				{/if}{parts.message}</span
			>{/each}</pre>
	{#if following && !pinned}
		<button class="btn absolute right-4 bottom-4 shadow btn-primary btn-sm" type="button" onclick={jump}>
			<ArrowDownToLine size={14} /> Jump to latest
		</button>
	{/if}
</div>
{#if shown.length !== lines.length}
	<p class="text-sm text-base-content/70">{shown.length} of {lines.length} lines match the filter.</p>
{/if}
