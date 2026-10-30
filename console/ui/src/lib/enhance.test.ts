import { afterEach, describe, expect, it, vi } from 'vitest';
import { Submission, type Settled } from './enhance.svelte';
import { dismiss, toasts } from './toast.svelte';

/** Run one submission with the answer of the server and the page update that SvelteKit gives it. */
async function run(submission: Submission, result: Settled['result']) {
	const update = vi.fn(() => Promise.resolve());
	const callback = submission.submit();
	const pendingDuring = submission.pending;

	await callback({ result, update });

	return { update, pendingDuring };
}

afterEach(() => {
	for (const toast of [...toasts]) {
		dismiss(toast.id);
	}

	vi.useRealTimers();
});

describe('Submission', () => {
	it('is pending from the submit until the page has its data, so a second click finds the button disabled', async () => {
		const submission = new Submission();
		const { pendingDuring } = await run(submission, {
			type: 'success',
			status: 200,
			location: '/',
			data: { toast: 'Saved.' }
		});

		expect(pendingDuring).toBe(true);
		expect(submission.pending).toBe(false);
	});

	it('shows the toast of a success, and runs the hooks in order', async () => {
		const order: string[] = [];
		const submission = new Submission({
			onsuccess: () => order.push('success'),
			onupdated: () => order.push('updated')
		});
		const { update } = await run(submission, {
			type: 'success',
			status: 200,
			location: '/',
			data: { toast: 'Saved.' }
		});

		expect(toasts.map((toast) => toast.text)).toEqual(['Saved.']);
		expect(order).toEqual(['success', 'updated']);
		expect(update).toHaveBeenCalledOnce();
	});

	it('carries the run of a toast', async () => {
		await run(new Submission(), {
			type: 'success',
			status: 200,
			location: '/',
			data: { toast: 'Run 7 started.', run: '7' }
		});

		expect(toasts[0]?.run).toBe('7');
	});

	it('undoes the change of begin after a refusal, and keeps the message of an inline dialog', async () => {
		const gone = new Set(['row']);
		const submission = new Submission({
			inline: true,
			begin: () => () => gone.delete('row')
		});
		const { update } = await run(submission, {
			type: 'failure',
			status: 400,
			location: '/',
			data: { message: 'The value is wrong' }
		});

		expect(gone.size).toBe(0);
		expect(submission.error).toBe('The value is wrong');
		expect(update).not.toHaveBeenCalled();
	});

	it('leaves the refusal to the page when the form is not inline', async () => {
		const submission = new Submission();
		const { update } = await run(submission, { type: 'failure', status: 400, location: '/', data: { message: 'No' } });

		expect(submission.error).toBeNull();
		expect(update).toHaveBeenCalledOnce();
	});

	it('clears the last message when a new submit starts', async () => {
		const submission = new Submission({ inline: true });

		await run(submission, { type: 'failure', status: 400, location: '/', data: { message: 'No' } });
		await run(submission, { type: 'success', status: 200, location: '/', data: { toast: 'Saved.' } });

		expect(submission.error).toBeNull();
	});
});
