import { describe, expect, it } from 'vitest';
import { controlMessage, startRunMessage, writeMessage } from './messages';

describe('writeMessage', () => {
	it('shows the reason of the database for a wrong value or a clash', () => {
		expect(writeMessage({ ok: false, status: 400, message: 'The value is wrong: bad cron' })).toBe(
			'The value is wrong: bad cron'
		);
		expect(writeMessage({ ok: false, status: 409, message: 'The row clashes: duplicate key' })).toBe(
			'The row clashes: duplicate key'
		);
	});

	it('says nothing about a refusal beyond the fact', () => {
		expect(writeMessage({ ok: false, status: 403, message: "You aren't allowed to do this." })).toBe(
			"You aren't allowed to do this."
		);
	});
});

describe('startRunMessage', () => {
	it('tells the user to try again when a schema is not ready', () => {
		expect(startRunMessage({ ok: false, status: 409, message: 'x' })).toBe(
			'The schema is not ready. Try again in a minute.'
		);
	});

	it('uses the general message for other failures', () => {
		expect(startRunMessage({ ok: false, status: 403, message: "You aren't allowed to do this." })).toBe(
			"You aren't allowed to do this."
		);
	});
});

describe('controlMessage', () => {
	it.each([
		[409, 'The run is not in a state that allows this. Reload the page.'],
		[404, 'The run does not exist any more.'],
		[403, "You aren't allowed to do this."]
	])('explains a %d', (status, message) => {
		expect(
			controlMessage({ ok: false, status, message: status === 403 ? "You aren't allowed to do this." : 'x' })
		).toBe(message);
	});
});
