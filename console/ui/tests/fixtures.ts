import { randomBytes } from 'node:crypto';
import { expect, type Page } from '@playwright/test';
import { test as base, createBdd, defineParameterType } from 'playwright-bdd';
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

/**
 * Run an action that ends with the toast "Saved." and wait for that toast. An older toast has the same text, and an
 * identical toast shows once, so the helper dismisses every toast first. The toast that it finds belongs to the action.
 */
export async function saved(page: Page, act: () => Promise<void>): Promise<void> {
	const status = page.getByRole('status');

	await status.getByRole('button', { name: 'Dismiss' }).evaluateAll((buttons) => {
		for (const button of buttons) {
			if (button instanceof HTMLElement) {
				button.click();
			}
		}
	});
	await expect(status).not.toContainText('Saved.');
	await act();
	await expect(status).toContainText('Saved.');
}

export const test = base.extend<{ world: World }>({
	world: async ({ page }, use) => {
		await page.addInitScript((seconds) => {
			try {
				localStorage.setItem('refresh-interval', seconds);
			} catch {
				return;
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

/**
 * The random part of the names in a scenario with the tag `@new-schema`. A worker runs one scenario at a time, so one
 * value for each worker is enough. A scenario without the tag has none, and its names stay as written.
 */
export const scenario = { suffix: '' };

/**
 * A schema or a table name in quotes. A scenario with a suffix gets its own name for each run, so scenarios and
 * repeats run in parallel without a clash. The user of the scenario has `*` as the claim, so any name is in it.
 */
defineParameterType({
	name: 'name',
	regexp: /"([^"]*)"/,
	transformer: (value: string) => (scenario.suffix === '' ? value : `${value}_${scenario.suffix}`)
});

export const { Given, When, Then, After, Before } = createBdd(test);

Before({ tags: '@new-schema' }, () => {
	scenario.suffix = randomBytes(3).toString('hex');
});
