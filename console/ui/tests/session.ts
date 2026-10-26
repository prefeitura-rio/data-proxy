import { join } from 'node:path';

/** The users that the scenarios sign in as. The setup project signs each of them in once. */
export const USERS = ['web_admin', 'web_viewer', 'web_other', 'web_api', 'web_policy', 'web_norole', 'web_root'];

export const PASSWORD = process.env.KEYCLOAK_USER_PASSWORD ?? 'web-test';

/** The file with the cookies of one user. Playwright deletes the folder before each run, so no session lasts. */
export const sessionFile = (user: string): string => join(process.cwd(), 'test-results', '.auth', `${user}.json`);
