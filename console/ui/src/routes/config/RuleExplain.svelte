<script lang="ts">
	import { resolve } from '$app/paths';
	import * as v from 'valibot';
	import { explain, GROUP, type Editor, type Explained } from '#lib/rules.js';
	import { PolicyRow } from '#lib/types.js';

	let { editor, schema, table }: { editor: Editor; schema: string; table: string | null } = $props();

	let subject = $state('');
	let answer = $state<Explained | null>(null);
	let message = $state('');
	let pending = $state(false);

	async function ask() {
		const name = subject.trim();

		if (name === '') {
			message = 'Enter a subject.';
			answer = null;

			return;
		}

		pending = true;
		message = '';

		try {
			const response = await fetch(
				`${resolve('/config/grants')}?subject=${encodeURIComponent(name)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table ?? '')}`
			);

			if (!response.ok) {
				message = response.status === 403 ? "You aren't allowed to read grants." : 'The grants could not be read.';
				answer = null;

				return;
			}

			const grants = v.safeParse(v.array(PolicyRow), await response.json());

			answer = grants.success ? explain(editor, grants.output) : null;
			message = grants.success ? '' : 'The answer had an unexpected shape.';
		} finally {
			pending = false;
		}
	}
</script>

<details class="rounded-box border border-base-300 p-3" data-testid="rule-explain">
	<summary class="cursor-pointer text-sm font-semibold">Explain for a subject</summary>
	<div class="flex flex-col gap-2 pt-2">
		<div class="flex items-end gap-2">
			<label class="flex flex-col gap-1 text-xs">
				<span class="text-base-content/70">Subject</span>
				<input class="input input-sm" name="explain_subject" bind:value={subject} placeholder="subject" />
			</label>
			<button class="btn btn-sm" type="button" disabled={pending} onclick={ask}>Explain</button>
		</div>
		<p class="text-xs text-base-content/60">
			It reads the grants of the subject in {schema}. It shows no row count, because a count needs a read of the data.
		</p>
		<div aria-live="polite" class="flex flex-col gap-2">
			{#if message}<p role="alert" class="text-sm text-error">{message}</p>{/if}
			{#if answer}
				<ul class="flex flex-col gap-1 text-sm">
					{#each answer.any as found (`${found.column}/${found.unit_type}`)}
						<li>
							{found.column} ({found.unit_type}):
							{found.values.length > 0 ? found.values.join(', ') : 'no grant'}
						</li>
					{/each}
					{#each answer.cards as card, index (index)}
						<li>
							<strong
								>{GROUP}
								{index + 1}: {card.active ? 'active' : `drops out, no grant of ${card.missing.join(', ')}`}</strong
							>
							<ul class="pl-4">
								{#each card.conditions as found (`${found.column}/${found.unit_type}`)}
									<li>
										{found.column} ({found.unit_type}):
										{found.values.length > 0 ? found.values.join(', ') : 'no grant'}
									</li>
								{/each}
							</ul>
						</li>
					{/each}
				</ul>
				<p class="rounded-box bg-base-200 p-3 text-sm" data-testid="rule-explain-rows">{answer.rows}</p>
			{/if}
		</div>
	</div>
</details>
