import { defineConfig } from '@playwright/test';
import { defineBddConfig } from 'playwright-bdd';

export default defineConfig({
	testDir: defineBddConfig({
		features: 'tests/features/*.feature',
		steps: ['tests/fixtures.ts', 'tests/steps/*.ts']
	}),
	grep: process.env.PLAYWRIGHT_GREP ? new RegExp(process.env.PLAYWRIGHT_GREP) : undefined,
	timeout: 600_000,
	workers: 4,
	use: {
		baseURL: process.env.CONSOLE_URL,
		actionTimeout: 15_000,
		navigationTimeout: 30_000
	}
});
