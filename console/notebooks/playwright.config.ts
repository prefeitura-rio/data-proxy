import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: 'tests',
	testMatch: '*.spec.ts',
	timeout: 240_000,
	workers: 1,
	use: { browserName: 'chromium' }
});
