import { expect, type Page } from '@playwright/test';
import * as v from 'valibot';
import { Grant } from '../../src/lib/types';
import { sql } from '../database';
import { After, Then, When } from '../fixtures';

const log = (page: Page) => page.locator('pre');

When('I open the run {string}', async ({ page }, id: string) => {
	await page.goto(`/sync/runs/${id}`);
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
});

When('I turn the {string} switch off', async ({ page }, name: string) => {
	await page.getByRole('checkbox', { name }).uncheck();
});

When('I turn the {string} switch on', async ({ page }, name: string) => {
	await page.getByRole('checkbox', { name }).check();
});

Then('the log does not show the line {string}', async ({ page }, text: string) => {
	// The stream reads every second, so a line that would arrive shows within this wait.
	await page.waitForTimeout(3000);
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
			async (body) => {
				const answer = await fetch('/query/grant', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify(body)
				});

				return { status: answer.status, text: await answer.text() };
			},
			{ schema, tables: tables.split(',') }
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

When('I try to add the table {string} to the schema {string}', async ({ page }, table: string, schema: string) => {
	await page.getByPlaceholder('project.dataset.table').fill(table);
	await page.locator('form[action="?/createTable"] select[name=schema]').selectOption(schema);
	await page.getByRole('button', { name: 'Add table' }).click();
});

const tableRow = (page: Page, table: string) =>
	page.getByRole('row', { name: new RegExp(`^${table.replaceAll('.', '\\.')} `) });

When(
	'I edit the table {string} to be partitioned with n {string} and fallback',
	async ({ page }, table: string, n: string) => {
		await tableRow(page, table).getByRole('button', { name: 'Edit' }).click();

		const dialog = page.locator('dialog[open]');

		await dialog.locator('select[name=strategy]').selectOption('partitioned');
		await dialog.locator('input[name=n]').fill(n);
		await dialog.getByLabel('fallback').check();
		await dialog.getByRole('button', { name: 'Save' }).click();
		await expect(page.getByRole('status')).toContainText('Saved.');
	}
);

When('I try to edit the table {string} with n {string}', async ({ page }, table: string, n: string) => {
	await tableRow(page, table).getByRole('button', { name: 'Edit' }).click();
	await page.locator('dialog[open] input[name=n]').fill(n);
	await page.locator('dialog[open]').getByRole('button', { name: 'Save' }).click();
});

When('I edit the table {string} with sort columns {string}', async ({ page }, table: string, sort: string) => {
	await tableRow(page, table).getByRole('button', { name: 'Edit' }).click();
	await page.locator('dialog[open] input[name=sort]').fill(sort);
	await page.locator('dialog[open]').getByRole('button', { name: 'Save' }).click();
	await expect(page.getByRole('status')).toContainText('Saved.');
});

Then('the table {string} shows {string}', async ({ page }, table: string, text: string) => {
	await expect(tableRow(page, table)).toContainText(text);
});

When('I mark the schema {string} as encrypted', async ({ page }, schema: string) => {
	await page
		.getByRole('row', { name: new RegExp(`^${schema} `) })
		.getByRole('button', { name: 'Edit' })
		.click();
	await page.locator('dialog[open]').getByLabel('encrypted').check();
	await page.locator('dialog[open]').getByRole('button', { name: 'Save' }).click();
	await expect(page.getByRole('status')).toContainText('Saved.');
});

When('I choose the schema {string}', async ({ page }, schema: string) => {
	await page.locator('select').selectOption(schema);
});

Then('I see the warning {string}', async ({ page }, text: string) => {
	await expect(page.getByText(text).first()).toBeVisible();
});

Then('the button {string} is disabled', async ({ page }, name: string) => {
	await expect(page.getByRole('button', { name })).toBeDisabled();
});

Then('I see the alert {string}', async ({ page }, text: string) => {
	await expect(page.getByRole('alert')).toContainText(text);
});

Then('I see an alert', async ({ page }) => {
	await expect(page.getByRole('alert')).toBeVisible();
});

When('I try to clear the claim of the schema {string}', async ({ page }, schema: string) => {
	await page
		.getByRole('row', { name: new RegExp(`^${schema} `) })
		.getByRole('button', { name: 'Edit' })
		.click();
	await page.locator('dialog[open] input[name=claim]').fill('');
	await page.locator('dialog[open]').getByRole('button', { name: 'Save' }).click();
});

Then('the schema {string} still has the claim {string}', async ({ page }, schema: string, claim: string) => {
	await expect(page.getByRole('row', { name: new RegExp(`^${schema} `) })).toContainText(claim);
});

When('I preview the import of a file with the text {string}', async ({ page }, text: string) => {
	await page
		.locator('input[type=file]')
		.setInputFiles({ name: 'broken.json', mimeType: 'application/json', buffer: Buffer.from(text) });
	await page.getByRole('button', { name: 'Preview import' }).click();
});

When('I remember the exported file', ({ world }) => {
	world.file = world.exported;
});

When('I use the remembered file', ({ world }) => {
	world.exported = world.file;
});

After({ tags: '@other-schema' }, async () => {
	await sql(`
		DELETE FROM console.rls WHERE table_name IN (SELECT name FROM console.tables WHERE schema = 'other');
		DELETE FROM console.tables WHERE schema = 'other';
		DELETE FROM console.schemas WHERE name = 'other'`);
});

After({ tags: '@import-ttl' }, async () => {
	await sql("UPDATE console.tables SET cache_ttl = NULL WHERE schema = 'test' AND cache_ttl = 322");
});
