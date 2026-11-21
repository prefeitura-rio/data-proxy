<script lang="ts">
	import { ArrowDownToLine, ArrowUpFromLine, Plus } from '@lucide/svelte';
	import { tick, untrack } from 'svelte';
	import FormDialog from '#lib/components/FormDialog.svelte';
	import type { Submission } from '#lib/enhance.svelte.js';
	import * as rules from '#lib/rules.js';
	import RuleExplain from './RuleExplain.svelte';
	import RuleRow from './RuleRow.svelte';

	let {
		table,
		schema,
		stored,
		submission,
		onclose
	}: {
		table: string | null;
		schema: string;
		stored: rules.Stored[];
		submission: Submission;
		onclose: () => void;
	} = $props();

	const { GROUP } = rules;
	const newId = () => crypto.randomUUID();
	const column = 'input[name=column]';

	let groups = $state<rules.Groups>({ any: [], cards: [] });
	let announce = $state('');
	let box = $state<HTMLElement>();

	/** Load the rules when the dialog opens. A refresh of the page gives a new `stored`, and it must not undo an edit. */
	$effect(() => {
		if (table !== null) {
			groups = untrack(() => rules.startGroups(stored, newId));
			announce = '';
		}
	});

	const sentence = $derived(rules.describe(rules.asEditor(groups)));
	const problems = $derived(rules.warnings(rules.asEditor(groups)));

	function change(next: rules.Groups, message: string, focus = column, first = false) {
		groups = next;
		announce = message;

		void tick().then(() => {
			const found = box?.querySelectorAll<HTMLInputElement>(focus);

			found?.[first ? 0 : found.length - 1]?.focus();
		});
	}

	function remove(entry: rules.Item) {
		change(rules.without(groups, entry.id), `Removed the condition ${entry.column}.`);
	}

	function addCard() {
		const id = newId();

		change(
			rules.withCard(groups, id, newId(), newId()),
			`Added ${GROUP} ${groups.cards.length + 1}.`,
			`[data-card="${id}"] ${column}`,
			true
		);
	}
</script>

<FormDialog open={table !== null} title="Row rules of {table ?? ''}" action="?/saveRules" {submission} {onclose}>
	<div bind:this={box} class="flex flex-col gap-3">
		<input type="hidden" name="table_name" value={table ?? ''} />

		{#each rules.fieldsOf(groups) as { entry, group } (entry.id)}
			<input type="hidden" name="rule_column" value={entry.column} />
			<input type="hidden" name="rule_unit_type" value={entry.unit_type} />
			<input type="hidden" name="rule_group" value={group} />
		{/each}

		<fieldset class="rounded-box border border-base-300 p-3" data-list="any">
			<legend class="px-1 text-sm font-semibold">Any of these is enough</legend>
			{#each groups.any as entry (entry.id)}
				<RuleRow
					{entry}
					onremove={() => {
						remove(entry);
					}}
				>
					{#snippet actions()}
						{#each groups.cards as card, index (card.id)}
							<button
								class="btn btn-ghost btn-xs"
								type="button"
								onclick={() => {
									change(rules.intoCard(groups, entry.id, card.id), `Moved ${entry.column} to ${GROUP} ${index + 1}.`);
								}}
							>
								<ArrowDownToLine size={14} /> Must also match in {GROUP}
								{index + 1}
							</button>
						{/each}
						<button
							class="btn btn-ghost btn-xs"
							type="button"
							onclick={() => {
								change(
									rules.intoNewCard(groups, entry.id, newId(), newId()),
									`Started a new ${GROUP.toLowerCase()} with ${entry.column}.`
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
					change(rules.withBlank(groups, newId(), null), 'Added a condition.', `[data-list=any] ${column}`);
				}}
			>
				<Plus size={14} /> Add a condition
			</button>
		</fieldset>

		{#each groups.cards as card, index (card.id)}
			<p class="text-center text-xs tracking-wide text-base-content/60 uppercase">or</p>
			<fieldset class="rounded-box border border-primary/40 p-3" data-card={card.id}>
				<legend class="px-1 text-sm font-semibold">{GROUP} {index + 1}: all of these must match</legend>
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
									change(rules.intoAlternatives(groups, entry.id), `Moved ${entry.column} to the alternatives.`);
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
							rules.withBlank(groups, newId(), card.id),
							`Added a condition to ${GROUP} ${index + 1}.`,
							`[data-card="${card.id}"] ${column}`
						);
					}}
				>
					<Plus size={14} /> Add a condition to {GROUP}
					{index + 1}
				</button>
			</fieldset>
		{/each}

		<button class="btn self-start btn-sm" type="button" onclick={addCard}>
			<Plus size={14} /> Add another {GROUP.toLowerCase()}
		</button>

		{#if problems.length > 0}
			<ul class="alert flex-col items-start alert-soft text-sm alert-warning" aria-label="Warnings">
				{#each problems as problem (problem)}<li>{problem}</li>{/each}
			</ul>
		{/if}

		<p class="rounded-box bg-base-200 p-3 text-sm" data-testid="rule-sentence">{sentence}</p>
		<RuleExplain editor={rules.asEditor(groups)} {schema} {table} />
		<p class="sr-only" aria-live="polite">{announce}</p>
	</div>
</FormDialog>
