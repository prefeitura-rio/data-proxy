import * as env from '$app/env/private';
import { querySettingsFrom, type QuerySettings } from './query-settings';

export const settings = () => ({
	issuer: env.OIDC_ISSUER,
	clientId: env.OIDC_CLIENT_ID,
	clientSecret: env.OIDC_CLIENT_SECRET,
	cookieSecret: env.COOKIE_SECRET,
	postgrestUrl: env.POSTGREST_URL.replace(/\/$/, ''),
	origin: env.ORIGIN
});

export type { QuerySettings };

/** The settings of the query tab, or null when one is missing, so that the other tabs still work. */
export const querySettings = (): QuerySettings | null => querySettingsFrom(env);
