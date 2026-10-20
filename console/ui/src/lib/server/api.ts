import { error } from '@sveltejs/kit';
import { postgrest, type Result } from './postgrest';
import { settings } from './settings';
import type { Session } from './session';

export const api = (session: Session) => postgrest(settings().postgrestUrl, session.accessToken);

/** Return the data of a read, or stop the page with the status of the refusal. */
export function need<T>(result: Result<T>): T {
	if (!result.ok) {
		error(result.status >= 400 && result.status < 600 ? result.status : 500, result.message);
	}

	return result.data;
}
