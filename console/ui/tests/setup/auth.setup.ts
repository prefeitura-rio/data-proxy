import { test as setup } from '@playwright/test';
import { PASSWORD, sessionFile, USERS } from '../session';

// One sign-in through the identity provider takes about a second. The scenarios share these sessions, so each user
// signs in once for the whole run and not once for each worker.
for (const user of USERS) {
	setup(`sign in as ${user}`, async ({ page, context }) => {
		await page.goto('/console');
		await page.fill('#username', user);
		await page.fill('#password', PASSWORD);
		await page.click('#kc-login');
		await page.waitForURL(/console\.localhost/);
		await context.storageState({ path: sessionFile(user) });
	});
}
