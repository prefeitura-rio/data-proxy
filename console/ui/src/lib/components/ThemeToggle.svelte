<script lang="ts">
	import { Moon, Sun } from '@lucide/svelte';
	import { currentTheme, saveTheme, toggled, type Theme } from '#lib/theme.js';

	let theme = $state<Theme>('light');

	/** The server cannot know the theme, so the button reads it when it appears. */
	function read() {
		theme = currentTheme();
	}

	function toggle() {
		theme = toggled(theme);
		document.documentElement.dataset.theme = theme;
		saveTheme(theme, localStorage);
	}
</script>

<button
	{@attach read}
	class="btn btn-circle btn-ghost btn-sm"
	type="button"
	onclick={toggle}
	aria-label="Switch to {toggled(theme)} theme"
>
	{#if theme === 'dark'}<Sun size={16} />{:else}<Moon size={16} />{/if}
</button>
