import { Given, When } from '../fixtures';

Given('the browser prefers the {string} theme', async ({ page }, theme: string) => {
	await page.emulateMedia({ colorScheme: theme === 'dark' ? 'dark' : 'light' });
});

When('I switch to the {string} theme', async ({ page }, theme: string) => {
	await page.getByRole('button', { name: `Switch to ${theme} theme` }).click();
});
