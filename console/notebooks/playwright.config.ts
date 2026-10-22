import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: 'tests',
	testMatch: '*.spec.ts',
	timeout: 240_000,
	fullyParallel: true,
	workers: 3,
	use: { browserName: 'chromium' }
});
