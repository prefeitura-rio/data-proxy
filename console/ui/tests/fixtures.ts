import { test as base, createBdd } from 'playwright-bdd';
import * as v from 'valibot';
import { Grant } from '../src/lib/types';

declare global {
	interface Window {
		/** The messages that the page received from its frames. */
		__m: { type: string }[];
	}
}

/** The path of the console on the host of the API. */
export const BASE = '/console';

/** What the steps of one scenario hand to each other. */
export type World = {
	lastStatus: number;
	grant: Grant | null;
	grants: Grant[];
	exported: string;
	file: string;
	runId: string;
	/** The lifespan that the identity provider had before a scenario changed it. */
	lifespan: string | null | undefined;
	cookies: string;
};

export const test = base.extend<{ world: World }>({
	world: async ({ page }, use) => {
		const world: World = {
			lastStatus: 0,
			grant: null,
			grants: [],
			exported: '',
			file: '',
			runId: '',
			lifespan: undefined,
			cookies: ''
		};

		page.on('response', (response) => {
			if (response.url().endsWith('/query/grant') && response.ok()) {
				void response.json().then((body: unknown) => world.grants.push(v.parse(Grant, body)));
			}
		});
		await page.addInitScript(() => {
			window.__m = [];
			window.addEventListener('message', (event: MessageEvent<{ type: string }>) => window.__m.push(event.data));
		});

		await use(world);
	}
});

export const { Given, When, Then, After } = createBdd(test);
