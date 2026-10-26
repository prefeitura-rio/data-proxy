import { expect, type Page } from '@playwright/test';
import { test as base, createBdd } from 'playwright-bdd';
import { Grant } from '../src/lib/types';

/** The path of the console on the host of the API. */
export const BASE = '/console';

/** What the steps of one scenario hand to each other. */
export type World = {
	lastStatus: number;
	/** The headers of the last page that a step loaded. */
	headers: Record<string, string>;
	grant: Grant | null;
	exported: string;
	file: string;
	runId: string;
	/** The lifespan that the identity provider had before a scenario changed it. */
	lifespan: string | null | undefined;
	cookies: string;
	/** The schemas that the scenario added, so its hook deletes them and no others. */
	schemas: string[];
};

/** The refresh interval of the Sync pages in the tests, in seconds. The default of the console is slower. */
const REFRESH_SECONDS = '2';

/**
 * Wait until the page runs its scripts. A field that a step fills before this point loses its value or its handler.
 * The sign-in page of the identity provider has no scripts to wait for, so it counts as ready too.
 */
export const hydrated = (page: Page) => page.locator('html[data-hydrated]').or(page.locator('#username')).waitFor();

export const test = base.extend<{ world: World }>({
	world: async ({ page }, use) => {
		await page.addInitScript((seconds) => {
			try {
				localStorage.setItem('refresh-interval', seconds);
			} catch {
				// The storage is blocked, so the page keeps its default.
			}
		}, REFRESH_SECONDS);

		const world: World = {
			lastStatus: 0,
			headers: {},
			grant: null,
			exported: '',
			file: '',
			runId: '',
			lifespan: undefined,
			cookies: '',
			schemas: []
		};

		const violations: string[] = [];

		page.on('console', (message) => {
			if (message.text().includes('Content Security Policy')) violations.push(message.text());
		});

		await use(world);

		expect(violations, 'The browser refused something under the security policy').toEqual([]);
	}
});

export const { Given, When, Then, After } = createBdd(test);
