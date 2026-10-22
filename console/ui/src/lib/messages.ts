import type { Result } from './server/postgrest';

export const writeMessage = (result: Result<unknown>) => (result.ok ? null : result.message);

export const startRunMessage = (result: Result<unknown>) => {
	if (result.ok) {
		return null;
	}

	return result.status === 409 ? 'The schema is not ready. Try again in a minute.' : result.message;
};

export const controlMessage = (result: Result<unknown>) => {
	if (result.ok) {
		return null;
	}

	if (result.status === 409) {
		return 'The run is not in a state that allows this. Reload the page.';
	}

	return result.status === 404 ? 'The run does not exist any more.' : result.message;
};
