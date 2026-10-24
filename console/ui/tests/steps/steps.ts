import { randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, type Page } from '@playwright/test';
import * as v from 'valibot';
import { Grant } from '../../src/lib/types';
import { BASE, Given, Then, When, type World } from '../fixtures';

/** Read the first bytes of a link. The link is signed for its own host, so the request keeps that host. */
async function statusOf(link: string | URL): Promise<number> {
	const response = await fetch(link, { headers: { range: 'bytes=0-3' } });

	return response.status;
}

/** The first data link of the grant that the last request returned. */
function firstLink(world: World): string {
	const link = world.grant?.tables[0]?.files[0]?.data;

	expect(link, 'The scenario has no grant').toBeDefined();

	return link ?? '';
}

const PASSWORD = process.env.KEYCLOAK_USER_PASSWORD ?? 'web-test';

Given('I sign in as {string}', async ({ page }, user: string) => {
	await page.goto(BASE);
	await page.fill('#username', user);
	await page.fill('#password', PASSWORD);
	await page.click('#kc-login');
	await page.waitForURL(/console\.localhost/);
});

When('I open the {string} tab', async ({ page }, tab: string) => {
	await page.getByRole('tab', { name: tab }).click();
	await expect(page.getByRole('tab', { name: tab })).toHaveClass(/tab-active/);
});

Then('I see the {string} tab', async ({ page }, tab: string) => {
	await expect(page.getByRole('tab', { name: tab })).toBeVisible();
});

Then('I do not see the {string} tab', async ({ page }, tab: string) => {
	await expect(page.getByRole('tab', { name: tab })).toHaveCount(0);
});

Then('I do not see the {string} button', async ({ page }, name: string) => {
	await expect(page.getByRole('button', { name })).toHaveCount(0);
});

/** Call the console from the page, so that the request carries the cookie and the origin of the browser. */
async function post(
	page: Page,
	path: string,
	init: { body: string; type: string }
): Promise<{ status: number; text: string }> {
	return page.evaluate(
		async ({ path, init }) => {
			const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': init.type }, body: init.body });

			return { status: response.status, text: await response.text() };
		},
		{ path, init }
	);
}

When('I post a run request directly', async ({ page, world }) => {
	const response = await post(page, `${BASE}/sync?/start`, {
		body: 'schemas=test&tables=',
		type: 'application/x-www-form-urlencoded'
	});
	world.lastStatus = response.status;
});

When('I start a run for the schema {string}', async ({ page }, schema: string) => {
	await page.getByRole('checkbox', { name: schema }).check();
	await page.getByRole('button', { name: 'Start run' }).click();
});

Then('I see that a run started', async ({ page, world }) => {
	const status = page.getByRole('status');

	await expect(status).toContainText(/Run \S+ started\./);
	world.runId = /Run (\S+) started\./.exec((await status.textContent()) ?? '')?.[1] ?? '';
	expect(world.runId).not.toBe('');
});

When('I open the run that started', async ({ page, world }) => {
	await page.getByRole('link', { name: world.runId.slice(0, 8) }).click();
	await page.waitForURL(new RegExp(`${BASE}/sync/runs/${world.runId}$`));
});

const SEEDED_RUN = '00000000-0000-4000-8000-0000000000f1';

When('I open the seeded run', async ({ page }) => {
	await page.goto(`${BASE}/sync/runs/${SEEDED_RUN}`);
});

const log = (page: Page) => page.locator('pre');

Then('the log shows {int} lines', async ({ page }, count: number) => {
	await expect
		.poll(async () => (await log(page).innerText()).split('\n').filter((line) => line.trim() !== '').length)
		.toBe(count);
});

Then('the log says {string}', async ({ page }, text: string) => {
	await expect(page.getByText(text)).toBeVisible();
});

When('I load more log lines', async ({ page }) => {
	await page.getByRole('button', { name: 'Load more lines' }).click();
});

Then('the log has no button to load more', async ({ page }) => {
	await expect(page.getByRole('button', { name: 'Load more lines' })).toHaveCount(0);
});

Then('I see the error {string}', async ({ page }, message: string) => {
	await expect(page.getByText(message)).toBeVisible();
});

When('I sign out', async ({ page }) => {
	await page.getByRole('button', { name: 'Sign out' }).click();
	await page.locator('#kc-logout').click();
});

Then('I am on the no-access page', async ({ page }) => {
	await expect(page.getByRole('heading', { name: 'No access' })).toBeVisible();
});

Then('I see the page of that run', async ({ page, world }) => {
	await expect(page.getByRole('heading', { name: `Run ${world.runId}` })).toBeVisible();
});

function listOf(page: Page, column: string) {
	const table = page.locator('table').filter({ has: page.getByRole('columnheader', { name: column, exact: true }) });

	return {
		rows: table.locator('tbody tr'),
		pager: table.locator('xpath=ancestor::div[contains(@class, "overflow-x-auto")][1]/following-sibling::nav[1]')
	};
}

Then('the list with the column {string} has {int} rows', async ({ page }, column: string, count: number) => {
	await expect(listOf(page, column).rows).toHaveCount(count);
});

Then('the list with the column {string} says {string}', async ({ page }, column: string, range: string) => {
	await expect(listOf(page, column).pager.getByTestId('range')).toContainText(range);
});

Then(
	'every row of the list with the column {string} shows {string}',
	async ({ page }, column: string, text: string) => {
		const { rows } = listOf(page, column);

		await expect(rows.first()).toBeVisible();
		await expect(rows.filter({ hasNotText: text })).toHaveCount(0);
	}
);

When(
	'I go to the {string} rows of the list with the column {string}',
	async ({ page }, which: string, column: string) => {
		await listOf(page, column).pager.getByRole('button', { name: which }).click();
	}
);

When(
	'I set {string} to {string} in the form {string} and submit it',
	async ({ page }, label: string, value: string, name: string) => {
		const form = page.getByRole('form', { name });
		const control = form.getByLabel(label);

		if ((await control.evaluate((element) => element.tagName)) === 'SELECT') {
			await control.selectOption(value);
		} else {
			await control.fill(value);
		}

		await Promise.all([
			page.waitForURL((url) => url.search !== ''),
			form.getByRole('button', { name: /Filter|Search/ }).click()
		]);
	}
);

const controls = (page: Page) => page.getByRole('form', { name: 'Control the run' });

const runIdOf = (address: string) => /\/sync\/runs\/([^/?#]+)/.exec(address)?.[1] ?? '';

When('I open the first run of the list', async ({ page, world }) => {
	const link = listOf(page, 'Started by').rows.first().getByRole('link');

	world.runId = runIdOf((await link.getAttribute('href')) ?? '');
	await link.click();
	await page.waitForURL(new RegExp(`${BASE}/sync/runs/${world.runId}$`));
});

When('I cancel the run', async ({ page }) => {
	await controls(page).getByRole('button', { name: 'Cancel' }).click();
});

When('I resume the run', async ({ page }) => {
	await controls(page).getByRole('button', { name: 'Resume' }).click();
});

When('I run the run again', async ({ page, world }) => {
	const before = world.runId;

	await controls(page).getByRole('button', { name: 'Run again' }).click();
	await page.waitForURL((url) => runIdOf(url.pathname) !== '' && runIdOf(url.pathname) !== before);
	world.runId = runIdOf(page.url());
});

Then('the run shows the status {string}', async ({ page }, status: string) => {
	await expect(page.locator('p', { hasText: 'Status:' })).toContainText(status, { timeout: 30_000 });
});

When('I post a cancel request for this run directly', async ({ page, world }) => {
	const response = await post(page, `${BASE}/sync/runs/${world.runId}?/cancel`, {
		body: '',
		type: 'application/x-www-form-urlencoded'
	});

	world.lastStatus = response.status;
});

When('I pause the schedule {string}', async ({ page }, name: string) => {
	await page
		.getByRole('form', { name: `Control the schedule ${name}` })
		.getByRole('button', { name: 'Pause' })
		.click();
});

When('I resume the schedule {string}', async ({ page }, name: string) => {
	await page
		.getByRole('form', { name: `Control the schedule ${name}` })
		.getByRole('button', { name: 'Resume' })
		.click();
});

Then('the schedule {string} shows the status {string}', async ({ page }, name: string, status: string) => {
	const row = page.getByRole('row').filter({ has: page.getByRole('form', { name: `Control the schedule ${name}` }) });

	await expect(row).toContainText(status, { timeout: 30_000 });
});

When('I post a pause request for the schedule {string} directly', async ({ page, world }, name: string) => {
	const response = await post(page, `${BASE}/sync?/pause`, {
		body: `name=${name}`,
		type: 'application/x-www-form-urlencoded'
	});

	world.lastStatus = response.status;
});

Then('the header shows {string}', async ({ page }, identity: string) => {
	await expect(page.getByTestId('identity')).toHaveText(identity);
});

Then('I see the {string} table', async ({ page }, heading: string) => {
	await expect(page.getByRole('heading', { name: heading })).toBeVisible();
});

Then('the run waits in the queue, started by {string}', async ({ page }, user: string) => {
	const summary = page.locator('p', { hasText: 'Status:' });

	await expect(summary).toContainText('ENQUEUED');
	await expect(summary).toContainText(`Started by ${user}`);
});

Then('the answer is {int}', ({ world }, status: number) => {
	expect(world.lastStatus).toBe(status);
});

Then('the health check answers without a session', async ({ browser, baseURL }) => {
	const page = await (await browser.newContext({ baseURL })).newPage();
	await page.goto(`${BASE}/healthz`);

	expect(new URL(page.url()).pathname).toBe(`${BASE}/healthz`);
	await expect(page.locator('body')).toHaveText('ok');
});

Then('I see no row of the schema {string}', async ({ page }, schema: string) => {
	const named = page.getByRole('cell', { name: schema, exact: true });

	await expect(listOf(page, 'Encrypted').rows.filter({ has: named })).toHaveCount(0);
});

When('the session cookie is damaged and the identity provider has no session', async ({ context }) => {
	const cookies = await context.cookies();
	await context.clearCookies({ name: /^session\./ });
	await context.clearCookies({
		domain: new URL(process.env.KEYCLOAK_URL ?? '').hostname
	});
	await context.addCookies(
		cookies.filter((cookie) => cookie.name.startsWith('session.')).map((cookie) => ({ ...cookie, value: 'damaged' }))
	);
});

When('I reload the page', async ({ page }) => {
	await page.reload();
});

Then('I am on the sign-in page', async ({ page }) => {
	await expect(page.locator('#username')).toBeVisible();
});

When('I ask to delete the schema {string}', async ({ page }, schema: string) => {
	await page
		.getByRole('row', { name: new RegExp(schema) })
		.getByRole('button', { name: 'Delete' })
		.click();
});

Then('the confirm button stays disabled until I type {string}', async ({ page }, name: string) => {
	const confirm = page.locator('dialog').getByRole('button', { name: 'Delete' });
	await expect(confirm).toBeDisabled();
	await page.locator('dialog input[name=confirm]').fill('wrong');
	await expect(confirm).toBeDisabled();
	await page.locator('dialog input[name=confirm]').fill(name);
	await expect(confirm).toBeEnabled();
});

When('I add the schema {string}', async ({ page }, name: string) => {
	await page.getByPlaceholder('name', { exact: true }).first().fill(name);
	await page.getByRole('button', { name: 'Add schema' }).click();
	await expect(listOf(page, 'Encrypted').rows.getByRole('cell', { name, exact: true })).toBeVisible();
});

When(
	'I request a grant for the schema {string} and the table {string}',
	async ({ page, world }, schema: string, table: string) => {
		const response = await post(page, `${BASE}/query/grant`, {
			body: JSON.stringify({ schema, tables: [table] }),
			type: 'application/json'
		});
		world.lastStatus = response.status;
		world.grant = response.status === 200 ? v.parse(Grant, JSON.parse(response.text)) : null;
	}
);

Then('the first link of the grant is readable', async ({ world }) => {
	expect(await statusOf(firstLink(world))).toBe(206);
});

Then('the same link with another key answers 403', async ({ world }) => {
	const url = new URL(firstLink(world));
	url.pathname = url.pathname.replace(/\/[^/]+$/, '/other-file.parquet');
	expect(await statusOf(url)).toBe(403);
});

Then('the same link with a changed signature answers 403', async ({ world }) => {
	const url = new URL(firstLink(world));
	url.searchParams.set('X-Amz-Signature', '0'.repeat(64));
	expect(await statusOf(url)).toBe(403);
});

Then('the links are signed with an identity of the schema, not with the shared reader', ({ world }) => {
	const credential = new URL(firstLink(world)).searchParams.get('X-Amz-Credential') ?? '';

	expect(credential).not.toBe('');
	expect(credential).not.toMatch(/^data-proxy-reader\//);
});

const row = (page: Page, name: string | RegExp) => page.getByRole('row', { name });

Then('I see the row of the schema {string}', async ({ page }, schema: string) => {
	const named = page.getByRole('cell', { name: schema, exact: true });

	await expect(listOf(page, 'Encrypted').rows.filter({ has: named }).first()).toBeVisible();
});

Then('I see the message {string}', async ({ page }, message: string) => {
	await expect(page.getByRole('status')).toContainText(message);
});

When('I edit the schema {string} with claim {string}', async ({ page }, schema: string, claim: string) => {
	await row(page, new RegExp(`^${schema} `))
		.getByRole('button', { name: 'Edit' })
		.click();
	await page.locator('dialog[open] input[name=claim]').fill(claim);
	await page.locator('dialog[open]').getByRole('button', { name: 'Save' }).click();
	await expect(row(page, new RegExp(`^${schema} `))).toContainText(claim);
});

When('I add the table {string} to the schema {string}', async ({ page }, table: string, schema: string) => {
	await page.getByPlaceholder('project.dataset.table').fill(table);
	await page.locator('form[action="?/createTable"] select[name=schema]').selectOption(schema);
	await page.getByRole('button', { name: 'Add table' }).click();
	await expect(listOf(page, 'Strategy').rows.getByRole('cell', { name: table, exact: true })).toBeVisible();
});

When('I edit the table {string} with cache TTL {string}', async ({ page }, table: string, ttl: string) => {
	await row(page, new RegExp(`^${table.replaceAll('.', '\\.')} `))
		.getByRole('button', { name: 'Edit' })
		.click();
	await page.locator('dialog[open] input[name=cache_ttl]').fill(ttl);
	await page.locator('dialog[open]').getByRole('button', { name: 'Save' }).click();
	await expect(page.getByRole('status')).toContainText('Saved.');
});

Then('the table {string} shows cache TTL {string}', async ({ page }, table: string, ttl: string) => {
	await expect(
		row(page, new RegExp(`^${table.replaceAll('.', '\\.')} `)).getByRole('cell', { name: ttl, exact: true })
	).toBeVisible();
});

When(
	'I add a row rule for the table {string} on column {string} with unit type {string}',
	async ({ page }, table: string, column: string, unit: string) => {
		await page.locator('form[action="?/createRls"] select[name=table_name]').selectOption(table);
		await page.locator('form[action="?/createRls"] input[name=column_name]').fill(column);
		await page.locator('form[action="?/createRls"] input[name=unit_type]').fill(unit);
		await page.getByRole('button', { name: 'Add rule' }).click();
		await expect(page.getByRole('status')).toContainText('Saved.');
	}
);

When('I change the unit type of the rule on {string} to {string}', async ({ page }, table: string, unit: string) => {
	await page
		.getByRole('row', { name: new RegExp(`^${table.replaceAll('.', '\\.')} id `) })
		.getByRole('button', { name: 'Edit' })
		.click();
	await page.locator('dialog[open] input[name=unit_type]').fill(unit);
	await page.locator('dialog[open]').getByRole('button', { name: 'Save' }).click();
});

Then('I see a row rule on {string} with unit type {string}', async ({ page }, table: string, unit: string) => {
	await expect(
		page.getByRole('row', { name: new RegExp(`^${table.replaceAll('.', '\\.')} id ${unit}`) })
	).toBeVisible();
});

When('I delete the row rule of {string}', async ({ page }, table: string) => {
	await page
		.getByRole('row', { name: new RegExp(`^${table.replaceAll('.', '\\.')} id `) })
		.getByRole('button', { name: 'Delete' })
		.click();
});

Then('I see no row rule on {string}', async ({ page }, table: string) => {
	await expect(page.getByRole('row', { name: new RegExp(`^${table.replaceAll('.', '\\.')} id `) })).toHaveCount(0);
});

When('I delete the table {string}', async ({ page }, table: string) => {
	await row(page, new RegExp(`^${table.replaceAll('.', '\\.')} `))
		.getByRole('button', { name: 'Delete' })
		.click();
	await page.locator('dialog[open]').getByRole('button', { name: 'Delete' }).click();
});

Then('I see no table {string}', async ({ page }, table: string) => {
	await expect(listOf(page, 'Strategy').rows.getByRole('cell', { name: table, exact: true })).toHaveCount(0);
});

When('I delete the schema {string} by typing its name', async ({ page }, schema: string) => {
	await row(page, new RegExp(`^${schema} `))
		.getByRole('button', { name: 'Delete' })
		.click();
	await page.locator('dialog[open] input[name=confirm]').fill(schema);
	await page.locator('dialog[open]').getByRole('button', { name: 'Delete' }).click();
});

When('I export the configuration', async ({ page, world }) => {
	const download = page.waitForEvent('download');
	await page.getByRole('link', { name: 'Export' }).click();
	world.exported = await (await download).path();
});

Then('the exported file lists the schema {string}', async ({ world }, schema: string) => {
	expect(await readFile(world.exported, 'utf8')).toContain(`"${schema}"`);
});

When('I preview the import of the exported file', async ({ page, world }) => {
	await page.locator('input[type=file]').setInputFiles(world.exported);
	await page.getByRole('button', { name: 'Preview import' }).click();
	await expect(page.getByRole('region', { name: 'Import preview' })).toBeVisible();
});

When('I apply the previewed import', async ({ page }) => {
	await page.getByRole('button', { name: 'Apply import' }).click();
});

When('I set the cache TTL of every table in the exported file to {int}', async ({ world }, ttl: number) => {
	const Document = v.looseObject({
		schemas: v.record(v.string(), v.looseObject({ tables: v.array(v.looseObject({ name: v.string() })) }))
	});
	const document = v.parse(Document, JSON.parse(await readFile(world.exported, 'utf8')));
	const changed = {
		...document,
		schemas: Object.fromEntries(
			Object.entries(document.schemas).map(([name, schema]) => [
				name,
				{ ...schema, tables: schema.tables.map((table) => ({ ...table, cache_ttl: ttl })) }
			])
		)
	};
	const file = join(tmpdir(), `changed-${randomUUID()}.json`);

	await writeFile(file, JSON.stringify(changed));
	world.exported = file;
});

Then('the preview says {string}', async ({ page }, text: string) => {
	await expect(page.getByTestId('preview-summary')).toContainText(text);
});

Then('the preview lists an {string}', async ({ page }, action: string) => {
	await expect(
		page.getByRole('region', { name: 'Import preview' }).locator('tbody tr').filter({ hasText: action }).first()
	).toBeVisible();
});

Then('no table shows the cache TTL {string}', async ({ page }, ttl: string) => {
	await expect(listOf(page, 'Strategy').rows.getByRole('cell', { name: ttl, exact: true })).toHaveCount(0);
});

Then(
	'the history lists {string} of {string} {string} by {string}',
	async ({ page }, action: string, object: string, key: string, user: string) => {
		const row = page
			.getByRole('row')
			.filter({ hasText: user })
			.filter({ hasText: action })
			.filter({ hasText: object })
			.filter({ hasText: key });

		await expect(row.first()).toBeVisible();
	}
);

Then('the history lists no row of the schema {string}', async ({ page }, schema: string) => {
	await expect(listOf(page, 'Action').rows.first()).toBeVisible();
	await expect(
		page.getByRole('row').filter({ has: page.getByRole('cell', { name: schema, exact: true }) })
	).toHaveCount(0);
});

When('I add the policy {string} for {string} {string}', async ({ page }, subject: string, unit: string, id: string) => {
	const form = page.locator('form[action^="?/add"]');
	await form.locator('input[name=subject]').fill(subject);
	await form.locator('input[name=unit_type]').fill(unit);
	await form.locator('input[name=unit_id]').fill(id);
	await form.getByRole('button', { name: 'Add' }).click();
	await expect(page.getByRole('status')).toContainText('Saved.');
});

const policy = (page: Page, subject: string) =>
	page.locator('form[action^="?/edit"]').filter({ has: page.getByLabel(`Subject of ${subject}`, { exact: true }) });

Then('I see the policy {string} with unit id {string}', async ({ page }, subject: string, id: string) => {
	await expect(policy(page, subject).getByLabel(`Unit id of ${subject}`)).toHaveValue(id);
});

When('I change the unit id of the policy {string} to {string}', async ({ page }, subject: string, id: string) => {
	await policy(page, subject).getByLabel(`Unit id of ${subject}`).fill(id);
	await policy(page, subject).getByRole('button', { name: 'Save' }).click();
	await expect(page.getByRole('status')).toContainText('Saved.');
});

When('I remove the policy {string}', async ({ page }, subject: string) => {
	await policy(page, subject).getByRole('button', { name: 'Delete' }).click();
	await expect(page.getByRole('status')).toContainText('Saved.');
});

Then('I see no policy {string}', async ({ page }, subject: string) => {
	await expect(policy(page, subject)).toHaveCount(0);
});

Then('the access log lists {string} with the action {string}', async ({ page }, subject: string, action: string) => {
	await expect(row(page, new RegExp(`${action} ${subject}`)).first()).toBeVisible();
});

When('I confirm the deletion', async ({ page }) => {
	await page.locator('dialog[open]').getByRole('button', { name: 'Delete' }).click();
});

const frameOf = (page: Page) => page.frameLocator('iframe[title=Notebook]');
const messages = (page: Page) => page.evaluate(() => window.__m.map((message) => message.type));

When('I choose the table {string} of the schema {string}', async ({ page }, table: string, schema: string) => {
	await page.locator('select').selectOption(schema);
	await page.getByLabel(table, { exact: true }).check();
});

When('I open the notebook', async ({ page }) => {
	await page.getByRole('button', { name: 'Open notebook' }).click();
	await expect(page.locator('iframe[title=Notebook]')).toBeVisible();
});

Then('the grant has a delete file', async ({ world }) => {
	await expect.poll(() => world.grants.length, { timeout: 120_000 }).toBeGreaterThan(0);
	const files = world.grants.flatMap((grant) => grant.tables.flatMap((table) => table.files));

	expect(files.some((file) => file.delete !== null)).toBe(true);
});

When('I run the cells of the notebook', async ({ page }) => {
	const frame = frameOf(page);
	const cells = frame.locator('[data-status]').filter({ has: frame.locator('[data-testid="run-button"]') });
	// The setup block, the function that queries, the cell that waits for the grant, and the cell that shows the tables.
	await expect(cells).toHaveCount(4, { timeout: 120_000 });

	// The third cell waits for the first grant, so it ends when the grant has arrived and the query has run.
	for (const index of [0, 1, 2, 3]) {
		await cells.nth(index).locator('[data-testid="run-button"]').first().dispatchEvent('click');
		await expect(cells.nth(index)).toHaveAttribute('data-status', 'idle', { timeout: 180_000 });
	}
});

Then('the notebook has applied the grant', async ({ page }) => {
	await expect.poll(() => messages(page), { timeout: 180_000 }).toContain('applied');
});

When('I count the rows of the table {string} in the notebook', async ({ page }, table: string) => {
	const view = table.replace(/^.*\./, '');
	await frameOf(page).locator('.cm-content').last().click();
	await page.keyboard.press('Control+A');
	await page.keyboard.insertText(`df = await sql('SELECT count(*) AS n FROM "${view}"')\nprint(f"ROWS={df['n'][0]}")`);
	await page.keyboard.press('Shift+Enter');
});

Then('the notebook shows the number of rows that the database counts', async ({ page }) => {
	const file = process.env.EXPECTED_ROWS_FILE;

	expect(file, 'EXPECTED_ROWS_FILE names the file that the seed task writes').toBeTruthy();

	const expected = (await readFile(file ?? '', 'utf8')).trim();

	await expect(frameOf(page).locator('body')).toContainText(`ROWS=${expected}`, { timeout: 60_000 });
});

Then('the notebook gets a renewed grant within {int} seconds', async ({ page }, seconds: number) => {
	await expect
		.poll(async () => (await messages(page)).filter((type) => type === 'applied').length, {
			timeout: seconds * 1000,
			intervals: [1_000]
		})
		.toBeGreaterThan(1);
});

When('the first grant has expired', async ({ page, world }) => {
	const first = world.grants[0];

	expect(first, 'The page received no grant').toBeDefined();
	await page.waitForTimeout(Math.max(0, (first?.expiresAt ?? 0) - Date.now() + 5_000));
});

When('I save the notebook', async ({ page }) => {
	await frameOf(page).locator('.cm-content').last().click();
	await page.keyboard.press('Control+S');
});

When('I download the notebook as Python', async ({ page, world }) => {
	const frame = frameOf(page);
	await frame.getByTestId('notebook-menu-dropdown').click({ force: true });
	await frame.getByText('Export…').click();
	const dialog = frame.getByRole('dialog');
	await dialog.getByText('Python', { exact: true }).click();
	const download = page.waitForEvent('download');
	await dialog.getByRole('button', { name: 'Export notebook source' }).dispatchEvent('click');
	world.file = await (await download).path();
});

Then('the downloaded file has the cells and no signature of a link', async ({ world }) => {
	const text = await readFile(world.file, 'utf8');

	expect(text).toContain('dataProxy');
	expect(text).not.toMatch(/X-Amz-Signature|X-Amz-Credential/);
});
