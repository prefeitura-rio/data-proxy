import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { dismiss, notify, success, toasts, TOAST_MS } from './toast.svelte';

describe('toasts', () => {
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		for (const toast of [...toasts]) {
			dismiss(toast.id);
		}

		vi.useRealTimers();
	});

	it('keeps the toasts in the order that they came', () => {
		success('First.');
		success('Second.');

		expect(toasts.map((toast) => toast.text)).toEqual(['First.', 'Second.']);
	});

	it('shows the same text once while it is on screen', () => {
		success('Saved.');
		success('Saved.');

		expect(toasts).toHaveLength(1);
	});

	it('shows the text again after it left', () => {
		success('Saved.');
		vi.advanceTimersByTime(TOAST_MS);
		success('Saved.');

		expect(toasts).toHaveLength(1);
	});

	it('removes a toast after its time', () => {
		success('Saved.');
		vi.advanceTimersByTime(TOAST_MS - 1);

		expect(toasts).toHaveLength(1);

		vi.advanceTimersByTime(1);

		expect(toasts).toHaveLength(0);
	});

	it('keeps an error as long as it is told to', () => {
		notify({ kind: 'error', text: 'Failed.' }, 10_000);
		vi.advanceTimersByTime(TOAST_MS);

		expect(toasts).toHaveLength(1);
	});

	it('carries the run to link to', () => {
		success('Run abc started.', 'abc');

		expect(toasts[0]?.run).toBe('abc');
	});
});
