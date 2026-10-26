/** The current time in milliseconds, updated each second while `active()` is true. Call it while a component starts. */
export function clock(active: () => boolean) {
	let now = $state(Date.now());

	$effect(() => {
		if (!active()) {
			return;
		}

		const timer = setInterval(() => {
			now = Date.now();
		}, 1000);

		return () => {
			clearInterval(timer);
		};
	});

	return {
		get now() {
			return now;
		}
	};
}
