<script lang="ts">
	let { sections }: { sections: { id: string; label: string }[] } = $props();

	let active = $state('');

	/** The section that crosses the upper part of the window is the current one. */
	function watch() {
		const observer = new IntersectionObserver(
			(entries) => {
				const visible = entries.find((entry) => entry.isIntersecting);

				if (visible) {
					active = visible.target.id;
				}
			},
			{ rootMargin: '-100px 0px -70% 0px' }
		);

		for (const { id } of sections) {
			const element = document.getElementById(id);

			if (element) {
				observer.observe(element);
			}
		}

		return () => {
			observer.disconnect();
		};
	}
</script>

<nav
	{@attach watch}
	aria-label="On this page"
	class="sticky top-14 z-[5] -mx-4 mb-6 flex gap-1 overflow-x-auto border-b border-base-300 bg-base-100/90 px-4 backdrop-blur"
>
	{#each sections as section (section.id)}
		<a
			class={['tab', active === section.id && 'tab-active']}
			aria-current={active === section.id ? 'location' : undefined}
			href={`#${section.id}`}>{section.label}</a
		>
	{/each}
</nav>
