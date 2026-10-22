import { TTL_SECONDS } from './grant';
import type { QuerySettings } from './settings';

/** What the query tab shows. Without settings it has no notebook, and the page says that the tab isn't set up. */
export const queryView = (settings: QuerySettings | null) => ({
	notebookOrigin: settings?.notebookOrigin ?? null,
	grantTtlSeconds: settings?.signer.ttlSeconds ?? TTL_SECONDS
});
