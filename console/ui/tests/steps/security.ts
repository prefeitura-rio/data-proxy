import { expect, type BrowserContext } from '@playwright/test';
import { setTokenLifespan } from '../keycloak';
import { After, BASE, Given, Then, When } from '../fixtures';

When('I post a run request from the origin {string}', async ({ page, world }, origin: string) => {
	const response = await page.request.post(`${BASE}/sync?/start`, {
		headers: { Origin: origin, 'Content-Type': 'application/x-www-form-urlencoded' },
		data: 'schemas=test&tables='
	});

	world.lastStatus = response.status();
});

When('I load the page {string}', async ({ page, world }, address: string) => {
	const response = await page.request.get(`${BASE}${address}`);

	world.headers = response.headers();
});

When('I open the address {string} directly', async ({ page, world }, address: string) => {
	const response = await page.request.get(`${BASE}${address}`);

	world.lastStatus = response.status();
});

Then('the page has the header {string} with the value {string}', ({ world }, name: string, value: string) => {
	expect(world.headers[name]).toBe(value);
});

Then(
	'the page has the header {string} with a value that contains {string}',
	({ world }, name: string, part: string) => {
		expect(world.headers[name]).toContain(part);
	}
);

Then('an address without a session leads to the sign-in page', async ({ browser, baseURL }) => {
	const context = await browser.newContext({ baseURL });
	const page = await context.newPage();

	await page.goto(`${BASE}/sync`);
	await expect(page.locator('#username')).toBeVisible();
	await context.close();
});

Given('the identity provider issues tokens that last {int} seconds', async ({ world }, seconds: number) => {
	world.lifespan = await setTokenLifespan(String(seconds));
});

After({ tags: '@short-token' }, async ({ world }) => {
	await setTokenLifespan(world.lifespan ?? null);
});

const sessionCookies = async (context: BrowserContext) =>
	(await context.cookies())
		.filter((cookie) => cookie.name.startsWith('session.'))
		.map((cookie) => `${cookie.name}=${cookie.value}`)
		.sort()
		.join(';');

When('I remember the session cookies', async ({ context, world }) => {
	world.cookies = await sessionCookies(context);
	expect(world.cookies).not.toBe('');
});

Then('the session cookies are not the remembered ones', async ({ context, world }) => {
	const now = await sessionCookies(context);

	expect(now).not.toBe('');
	expect(now).not.toBe(world.cookies);
});
