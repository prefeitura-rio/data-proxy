/**
 * The browser tests of the console. The task `console:test:int` runs them against the stack of `nix/stack/`.
 *
 * Projects: `auth` signs each test user in once and saves the cookies in `test-results/.auth/`. `parallel` runs every
 * scenario in parallel. `shared` runs last, on one worker, for the scenarios that change data that every other
 * scenario reads: the schedule, the token lifespan and the sync document (`tests/features/shared/`).
 *
 * Scenarios: the steps `I sign in as` and `I am signed in as` add the saved cookies to the context, so a scenario
 * needs no round trip to the identity provider. A scenario with the tag `@fresh-login` signs in through the page.
 * The tag `@new-schema` adds a random suffix to each schema and table name (the step parameter `{name}`), signs in as
 * `web_root` (the `schemas` claim `*`), and deletes the schemas that the scenario added. The tag `@import-ttl`
 * restores the cache lifetime of the `test` tables, and `@short-token` restores the token lifespan of the Keycloak
 * client. The layout sets `data-hydrated` on `<html>`, and a step that types waits for it.
 *
 * Run some scenarios with `PLAYWRIGHT_GREP='health check'`, and pass other options with `PLAYWRIGHT_ARGS`. The report
 * of the last run is `test-results/report.json`. `--repeat-each` doesn't suit `shared`, because it runs the copies on
 * different workers.
 */
import { defineConfig } from '@playwright/test';
import { defineBddProject } from 'playwright-bdd';

const steps = ['tests/fixtures.ts', 'tests/steps/*.ts'];

export default defineConfig({
	projects: [
		{ name: 'auth', testDir: 'tests/setup', testMatch: /auth\.setup\.ts/, fullyParallel: true },
		{
			...defineBddProject({ name: 'parallel', features: 'tests/features/*.feature', steps }),
			fullyParallel: true,
			dependencies: ['auth']
		},
		// These scenarios change data that every other scenario reads: the schedule, the token lifespan and the sync
		// document. One worker runs them in order, after every other scenario.
		{
			...defineBddProject({ name: 'shared', features: 'tests/features/shared/*.feature', steps }),
			dependencies: ['parallel']
		}
	],
	grep: process.env.PLAYWRIGHT_GREP ? new RegExp(process.env.PLAYWRIGHT_GREP) : undefined,
	reporter: [['line'], ['json', { outputFile: 'test-results/report.json' }]],
	timeout: 60_000,
	expect: { timeout: 10_000 },
	workers: process.env.CI ? 2 : '50%',
	// A retry hides a flaky scenario, and a scenario must pass at its first try.
	retries: 0,
	maxFailures: process.env.CI ? 10 : undefined,
	use: {
		baseURL: process.env.CONSOLE_URL,
		actionTimeout: 15_000,
		navigationTimeout: 30_000,
		// The console and DaisyUI turn their animations off for this setting, and Playwright waits for an animation to end
		// before it clicks.
		contextOptions: { reducedMotion: 'reduce' },
		trace: 'on-first-retry',
		screenshot: 'only-on-failure'
	}
});
