import { expect, type Page } from '@playwright/test';
import * as v from 'valibot';
import { Grant } from '../../src/lib/types';
import { sql } from '../database';
import { After, BASE, Given, hydrated, saved, scenario, Then, When } from '../fixtures';

const STREAM_READ_MARGIN_MS = 3000;

const log = (page: Page) => page.locator('pre');

const streamOpened = (page: Page) => expect(page.getByText('Live', { exact: true })).toBeVisible();

When('I open the run {string}', async ({ page }, id: string) => {
	await page.goto(`${BASE}/sync/runs/${id}`);
	await hydrated(page);
});

When('I load more log lines {int} times', async ({ page }, count: number) => {
	for (let step = 1; step <= count; step++) {
		const expected = String(500 + step * 500);

		await Promise.all([
			page.waitForURL((url) => url.searchParams.get('lines') === expected),
			page.getByRole('button', { name: 'Load more lines' }).click()
		]);
	}
});

Then('the page has no {string} switch', async ({ page }, name: string) => {
	await expect(page.getByRole('checkbox', { name })).toHaveCount(0);
});

Then('the {string} switch is on', async ({ page }, name: string) => {
	await expect(page.getByRole('checkbox', { name })).toBeChecked();
	await streamOpened(page);
});

Given('the log stream of a run answers after {int} seconds', async ({ page }, seconds: number) => {
	await page.route(/\/sync\/runs\/[^/]+\/logs\?/, async (route) => {
		await new Promise((done) => setTimeout(done, seconds * 1000));
		await route.continue().catch(() => undefined);
	});
});

When('I refresh the data of the page', async ({ page }) => {
	await Promise.all([
		page.waitForResponse((response) => response.url().includes('__data.json')),
		page.getByRole('button', { name: 'Refresh now' }).click()
	]);
});

When('I turn the {string} switch off', async ({ page }, name: string) => {
	await page.getByRole('checkbox', { name }).uncheck();
});

When('I turn the {string} switch on', async ({ page }, name: string) => {
	await page.getByRole('checkbox', { name }).check();
});

Then('the log does not show the line {string}', async ({ page }, text: string) => {
	await page.waitForTimeout(STREAM_READ_MARGIN_MS);
	await expect(log(page)).not.toContainText(text);
});

Then('the log shows the line {string}', async ({ page }, text: string) => {
	await expect(log(page)).toContainText(text, { timeout: 20_000 });
});

When('a log line {string} is added to the run', async ({ world }, message: string) => {
	expect(world.runId).toMatch(/^[0-9a-f-]{36}$/);
	expect(message).toMatch(/^[a-z0-9 ]+$/);

	await sql(`
		INSERT INTO dbos.streams (workflow_uuid, key, value, "offset", serialization)
		SELECT '${world.runId}', 'logs',
			json_build_object('time', now(), 'level', 'INFO', 'message', '${message}', 'fields', '{}'::json,
				'schema', 'test', 'table', 'full_table')::text,
			coalesce(max("offset"), -1) + 1, 'portable_json'
		FROM dbos.streams WHERE workflow_uuid = '${world.runId}' AND key = 'logs'`);
});

When(
	'I request a grant for the schema {string} and the tables {string}',
	async ({ page, world }, schema: string, tables: string) => {
		const response = await page.evaluate(
			async ({ body, base }) => {
				const answer = await fetch(`${base}/query/grant`, {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify(body)
				});

				return { status: answer.status, text: await answer.text() };
			},
			{ body: { schema, tables: tables.split(',') }, base: BASE }
		);

		world.lastStatus = response.status;
		world.grant = response.status === 200 ? v.parse(Grant, JSON.parse(response.text)) : null;
	}
);

Then('the grant lists {int} tables', ({ world }, count: number) => {
	expect(world.grant?.tables).toHaveLength(count);
});

When('I try to add the schema {string}', async ({ page }, name: string) => {
	await page.getByPlaceholder('name', { exact: true }).first().fill(name);
	await page.getByRole('button', { name: 'Add schema' }).click();
});

When('I try to add the table {name} to the schema {name}', async ({ page }, table: string, schema: string) => {
	await page.getByPlaceholder('project.dataset.table').fill(table);
	await page.locator('form[action="?/createTable"] select[name=schema]').selectOption(schema);
	await page.getByRole('button', { name: 'Add table' }).click();
});

const tableRow = (page: Page, table: string) =>
	page.getByRole('row', { name: new RegExp(`^${table.replaceAll('.', '\\.')} `) });

When(
	'I edit the table {name} to be partitioned with n {string} and fallback',
	async ({ page }, table: string, n: string) => {
		await tableRow(page, table).getByRole('button', { name: 'Edit' }).click();

		const dialog = page.locator('dialog[open]');

		await dialog.locator('select[name=strategy]').selectOption('partitioned');
		await dialog.locator('input[name=n]').fill(n);
		await dialog.getByLabel('fallback').check();
		await saved(page, () => dialog.getByRole('button', { name: 'Save' }).click());
	}
);

When('I try to edit the table {name} with n {string}', async ({ page }, table: string, n: string) => {
	await tableRow(page, table).getByRole('button', { name: 'Edit' }).click();
	await page.locator('dialog[open] input[name=n]').fill(n);
	await page.locator('dialog[open]').getByRole('button', { name: 'Save' }).click();
});

When('I edit the table {name} with sort columns {string}', async ({ page }, table: string, sort: string) => {
	await tableRow(page, table).getByRole('button', { name: 'Edit' }).click();
	await page.locator('dialog[open] input[name=sort]').fill(sort);
	await saved(page, () => page.locator('dialog[open]').getByRole('button', { name: 'Save' }).click());
});

Then('the table {name} shows {string}', async ({ page }, table: string, text: string) => {
	await expect(tableRow(page, table)).toContainText(text);
});

When('I mark the schema {name} as encrypted', async ({ page }, schema: string) => {
	await page
		.getByRole('row', { name: new RegExp(`^${schema} `) })
		.getByRole('button', { name: 'Edit' })
		.click();
	await page.locator('dialog[open]').getByLabel('encrypted').check();
	await saved(page, () => page.locator('dialog[open]').getByRole('button', { name: 'Save' }).click());
});

When('I choose the schema {name}', async ({ page }, schema: string) => {
	await page.locator('select').selectOption(schema);
});

Then('I see the warning {string}', async ({ page }, text: string) => {
	await expect(page.getByText(text).first()).toBeVisible();
});

Then('the button {string} is disabled', async ({ page }, name: string) => {
	await expect(page.getByRole('button', { name })).toBeDisabled();
});

Then('the dialog stays open with an alert', async ({ page }) => {
	await expect(page.locator('dialog[open]').getByRole('alert')).toBeVisible();
});

Then('the button {string} is enabled', async ({ page }, name: string) => {
	await expect(page.getByRole('button', { name })).toBeEnabled();
});

Then('I see the hint {string}', async ({ page }, text: string) => {
	await expect(page.locator('#query-hint')).toHaveText(text);
});

When('I select all the tables', async ({ page }) => {
	await page.getByRole('button', { name: 'Select all' }).click();
});

Then('the schema {string} is chosen', async ({ page }, schema: string) => {
	await expect(page.locator('select')).toHaveValue(schema);
});

When('I filter the log by the level {string}', async ({ page }, level: string) => {
	await hydrated(page);
	await page.getByLabel('Level').selectOption(level);
});

When('I filter the log by the text {string}', async ({ page }, text: string) => {
	await hydrated(page);
	await page.getByLabel('Message contains').fill(text);
});

Then('I see the alert {string}', async ({ page }, text: string) => {
	await expect(page.getByRole('alert')).toContainText(text);
});

Then('I see an alert', async ({ page }) => {
	await expect(page.getByRole('alert')).toBeVisible();
});

When('I try to clear the claim of the schema {name}', async ({ page }, schema: string) => {
	await page
		.getByRole('row', { name: new RegExp(`^${schema} `) })
		.getByRole('button', { name: 'Edit' })
		.click();
	await page.locator('dialog[open] input[name=claim]').fill('');
	await page.locator('dialog[open]').getByRole('button', { name: 'Save' }).click();
});

Then('the schema {name} still has the claim {string}', async ({ page }, schema: string, claim: string) => {
	await expect(page.getByRole('row', { name: new RegExp(`^${schema} `) })).toContainText(claim);
});

When('I preview the import of a file with the text {string}', async ({ page }, text: string) => {
	await page
		.locator('input[type=file]')
		.setInputFiles({ name: 'broken.json', mimeType: 'application/json', buffer: Buffer.from(text) });
});

When('I remember the exported file', ({ world }) => {
	world.file = world.exported;
});

When('I use the remembered file', ({ world }) => {
	world.exported = world.file;
});

After({ tags: '@new-schema' }, async ({ world }) => {
	scenario.suffix = '';

	const names = world.schemas.filter((name) => /^[a-z][a-z0-9_]*$/.test(name)).map((name) => `'${name}'`);

	if (names.length === 0) {
		return;
	}

	await sql(`
		DELETE FROM console.rls WHERE table_name IN (SELECT name FROM console.tables WHERE schema IN (${names.join(', ')}));
		DELETE FROM console.tables WHERE schema IN (${names.join(', ')});
		DELETE FROM console.schemas WHERE name IN (${names.join(', ')})`);
});

After({ tags: '@import-ttl' }, async () => {
	await sql("UPDATE console.tables SET cache_ttl = NULL WHERE schema = 'test' AND cache_ttl = 322");
});
