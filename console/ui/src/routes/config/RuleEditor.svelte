<script lang="ts">
	import { ArrowDownToLine, ArrowUpFromLine, Plus } from '@lucide/svelte';
	import { tick } from 'svelte';
	import FormDialog from '#lib/components/FormDialog.svelte';
	import type { Submission } from '#lib/enhance.svelte.js';
	import * as rules from '#lib/rules.js';
	import RuleRow from './RuleRow.svelte';

	let {
		table,
		stored,
		submission,
		onclose
	}: {
		table: string | null;
		stored: rules.Stored[];
		submission: Submission;
		onclose: () => void;
	} = $props();

	const { WAY } = rules;
	const newId = () => crypto.randomUUID();
	const column = 'input[name=column]';

	let ways = $state<rules.Ways>({ any: [], cards: [] });
	let announce = $state('');
	let box = $state<HTMLElement>();

	$effect(() => {
		if (table !== null) {
			ways = rules.startWays(stored, newId);
			announce = '';
		}
	});

	const sentence = $derived(rules.describe(rules.asEditor(ways)));
	const problems = $derived(rules.warnings(rules.asEditor(ways)));

	function change(next: rules.Ways, message: string, focus = column, first = false) {
		ways = next;
		announce = message;

		void tick().then(() => {
			const found = box?.querySelectorAll<HTMLInputElement>(focus);

			found?.[first ? 0 : found.length - 1]?.focus();
		});
	}

	function remove(entry: rules.Item) {
		change(rules.without(ways, entry.id), `Removed the condition ${entry.column}.`);
	}

	function addCard() {
		const id = newId();

		change(
			rules.withCard(ways, id, newId(), newId()),
			`Added ${WAY} ${ways.cards.length + 1}.`,
			`[data-card="${id}"] ${column}`,
			true
		);
	}
</script>

<FormDialog open={table !== null} title="Row rules of {table ?? ''}" action="?/saveRules" {submission} {onclose}>
	<div bind:this={box} class="flex flex-col gap-3">
		<input type="hidden" name="table_name" value={table ?? ''} />

		{#each rules.fieldsOf(ways) as { entry, way } (entry.id)}
			<input type="hidden" name="rule_column" value={entry.column} />
			<input type="hidden" name="rule_unit_type" value={entry.unit_type} />
			<input type="hidden" name="rule_way" value={way} />
		{/each}

		<fieldset class="rounded-box border border-base-300 p-3" data-list="any">
			<legend class="px-1 text-sm font-semibold">Any of these is enough</legend>
			{#each ways.any as entry (entry.id)}
				<RuleRow
					{entry}
					onremove={() => {
						remove(entry);
					}}
				>
					{#snippet actions()}
						{#each ways.cards as card, index (card.id)}
							<button
								class="btn btn-ghost btn-xs"
								type="button"
								onclick={() => {
									change(rules.intoCard(ways, entry.id, card.id), `Moved ${entry.column} to ${WAY} ${index + 1}.`);
								}}
							>
								<ArrowDownToLine size={14} /> Must also match in {WAY}
								{index + 1}
							</button>
						{/each}
						<button
							class="btn btn-ghost btn-xs"
							type="button"
							onclick={() => {
								change(
									rules.intoNewCard(ways, entry.id, newId(), newId()),
									`Started a new ${WAY.toLowerCase()} with ${entry.column}.`
								);
							}}
						>
							<Plus size={14} /> Must also match something new
						</button>
					{/snippet}
				</RuleRow>
			{:else}
				<p class="py-1 text-sm text-base-content/60">No alternative yet.</p>
			{/each}
			<button
				class="btn mt-2 btn-ghost btn-xs"
				type="button"
				onclick={() => {
					change(rules.withBlank(ways, newId(), null), 'Added a condition.', `[data-list=any] ${column}`);
				}}
			>
				<Plus size={14} /> Add a condition
			</button>
		</fieldset>

		{#each ways.cards as card, index (card.id)}
			<p class="text-center text-xs tracking-wide text-base-content/60 uppercase">or</p>
			<fieldset class="rounded-box border border-primary/40 p-3" data-card={card.id}>
				<legend class="px-1 text-sm font-semibold">{WAY} {index + 1}: all of these must match</legend>
				{#each card.items as entry, position (entry.id)}
					{#if position > 0}<p class="py-1 text-xs text-base-content/60">and also</p>{/if}
					<RuleRow
						{entry}
						onremove={() => {
							remove(entry);
						}}
					>
						{#snippet actions()}
							<button
								class="btn btn-ghost btn-xs"
								type="button"
								onclick={() => {
									change(rules.intoAlternatives(ways, entry.id), `Moved ${entry.column} to the alternatives.`);
								}}
							>
								<ArrowUpFromLine size={14} /> Make it an alternative
							</button>
						{/snippet}
					</RuleRow>
				{/each}
				<button
					class="btn mt-2 btn-ghost btn-xs"
					type="button"
					onclick={() => {
						change(
							rules.withBlank(ways, newId(), card.id),
							`Added a condition to ${WAY} ${index + 1}.`,
							`[data-card="${card.id}"] ${column}`
						);
					}}
				>
					<Plus size={14} /> Add a condition to {WAY}
					{index + 1}
				</button>
			</fieldset>
		{/each}

		<button class="btn self-start btn-sm" type="button" onclick={addCard}>
			<Plus size={14} /> Add another {WAY.toLowerCase()}
		</button>

		{#if problems.length > 0}
			<ul class="alert flex-col items-start alert-soft text-sm alert-warning" aria-label="Warnings">
				{#each problems as problem (problem)}<li>{problem}</li>{/each}
			</ul>
		{/if}

		<p class="rounded-box bg-base-200 p-3 text-sm" data-testid="rule-sentence">{sentence}</p>
		<p class="sr-only" aria-live="polite">{announce}</p>
	</div>
</FormDialog>
