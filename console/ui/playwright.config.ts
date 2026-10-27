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
	retries: process.env.CI ? 1 : 0,
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
