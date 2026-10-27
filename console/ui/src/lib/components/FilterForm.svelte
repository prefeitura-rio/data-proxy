<script lang="ts">
	import { untrack, type Snippet } from 'svelte';

	/** The wait after the last key, in milliseconds, before the form applies its filters. */
	const DEBOUNCE_MS = 300;

	let {
		action,
		label,
		params,
		own,
		pages = [],
		submit = 'Filter',
		children
	}: {
		action: string;
		label: string;
		params: { toString(): string };
		/** The parameters that this form sets. The form keeps every other parameter of the address in hidden fields. */
		own: string[];
		/** The page parameters that a new filter resets. */
		pages?: string[];
		submit?: string;
		children: Snippet;
	} = $props();

	let form = $state<HTMLFormElement>();
	let timer: ReturnType<typeof setTimeout> | undefined;

	const kept = $derived(
		[...new URLSearchParams(params.toString())].filter(([key]) => !own.includes(key) && !pages.includes(key))
	);
	const active = $derived(own.some((key) => new URLSearchParams(params.toString()).has(key)));

	function later(event: Event) {
		const typing = event.type === 'input';

		if (!typing && event.target instanceof HTMLInputElement && event.target.type === 'text') {
			return;
		}

		clearTimeout(timer);
		timer = setTimeout(
			() => {
				form?.requestSubmit();
			},
			typing ? DEBOUNCE_MS : 0
		);
	}

	function clear() {
		for (const element of form?.elements ?? []) {
			if (element instanceof HTMLInputElement && element.type !== 'hidden') {
				element.value = '';
			} else if (element instanceof HTMLSelectElement) {
				element.value = '';
			}
		}

		form?.requestSubmit();
	}

	let seen = untrack(() => params.toString());

	/** A text field that the reader is not typing in follows the address when it changes, for example after the back button. */
	$effect(() => {
		const current = params.toString();

		if (current === seen) {
			return;
		}

		seen = current;

		for (const element of form?.elements ?? []) {
			if (element instanceof HTMLInputElement && element.type !== 'hidden' && element !== document.activeElement) {
				element.value = element.defaultValue;
			}
		}
	});
</script>

<form
	bind:this={form}
	method="GET"
	{action}
	class="toolbar"
	aria-label={label}
	data-sveltekit-keepfocus
	data-sveltekit-noscroll
	oninput={later}
	onchange={later}
	onsubmit={() => {
		clearTimeout(timer);
	}}
>
	{#each kept as [key, value] (`${key}=${value}`)}
		<input type="hidden" name={key} {value} />
	{/each}
	{@render children()}
	<button class="btn btn-primary btn-sm" type="submit">{submit}</button>
	{#if active}<button class="btn btn-sm" type="button" onclick={clear}>Clear</button>{/if}
</form>
