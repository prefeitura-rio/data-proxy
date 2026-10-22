import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { expect, type Page, test } from '@playwright/test';
import type { Status } from '../src/messages.ts';
import { startServers } from './servers.ts';

const root = fileURLToPath(new URL('..', import.meta.url));
let servers: Awaited<ReturnType<typeof startServers>>;

test.beforeAll(async () => {
	const data = join(root, 'test-results', `data-${test.info().parallelIndex}`);
	execFileSync('uv', ['run', '--frozen', '--group', 'test', 'python', 'tests/fixtures.py', data], {
		cwd: root,
		env: { ...process.env, UV_PROJECT_ENVIRONMENT: join(root, '.venv') }
	});
	servers = await startServers({ site: join(root, process.env.SITE ?? 'dist-check'), data });
});

test.afterAll(async () => {
	await servers.close();
});

const statuses = (page: Page): Promise<Status[]> => page.evaluate(() => window.statuses);

function checkValue(all: Status[], name: string): number | undefined {
	for (const status of all) {
		if (status.type === 'check' && status.check === name) {
			return status.value;
		}
	}

	return undefined;
}

async function openConsole(page: Page): Promise<Status[]> {
	await page.goto(servers.origin('console'));
	await expect
		.poll(async () => (await statuses(page)).filter((status) => status.type === 'check').length, { timeout: 150_000 })
		.toBe(3);

	return statuses(page);
}

test('runs under the policy with no blocked request, and applies the delete files', async ({ page }) => {
	const blocked: string[] = [];
	page.on('console', (message) => {
		if (message.text().includes('Content Security Policy')) {
			blocked.push(message.text());
		}
	});

	const all = await openConsole(page);

	expect(all.filter((status) => status.type === 'csp' || status.type === 'error')).toEqual([]);
	expect(blocked).toEqual([]);
	expect(checkValue(all, 'rows')).toBe(2700);
});

test('returns at most 100000 rows to Python unless the call asks for more', async ({ page }) => {
	const all = await openConsole(page);

	expect(checkValue(all, 'default_limit')).toBe(100_000);
	expect(checkValue(all, 'raised_limit')).toBe(150_000);
});

test('ignores a message from another origin', async ({ page }) => {
	await page.goto(`${servers.origin('notebook')}/`);
	await page.evaluate(() => {
		window.seen = [];
		new BroadcastChannel('status').onmessage = (event: MessageEvent<Status>) => {
			window.seen.push(event.data.type);
		};
	});
	await expect.poll(() => page.evaluate(() => window.seen), { timeout: 150_000 }).toContain('ready');

	await page.evaluate(() => {
		window.postMessage({ type: 'grant', grant: { schema: 'evil', tables: [], expiresAt: 0 } }, '*');
	});
	await page.waitForTimeout(3000);

	expect(await page.evaluate(() => window.seen)).not.toContain('applied');
});
