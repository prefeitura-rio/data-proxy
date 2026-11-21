import { describe, expect, it, vi } from 'vitest';
import { saveTheme, toggled } from './theme';

describe('toggled', () => {
	it.each([
		{ theme: 'light' as const, other: 'dark' },
		{ theme: 'dark' as const, other: 'light' }
	])('turns $theme into $other', ({ theme, other }) => {
		expect(toggled(theme)).toBe(other);
	});
});

describe('saveTheme', () => {
	it('stores the choice under the theme key', () => {
		const setItem = vi.fn();

		saveTheme('dark', { setItem });

		expect(setItem).toHaveBeenCalledExactlyOnceWith('theme', 'dark');
	});

	it('keeps going when the browser blocks its storage', () => {
		const setItem = vi.fn(() => {
			throw new DOMException('blocked', 'SecurityError');
		});

		expect(() => {
			saveTheme('light', { setItem });
		}).not.toThrow();
	});
});
