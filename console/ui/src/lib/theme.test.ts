import { describe, expect, it } from 'vitest';
import { themeOf, toggled } from './theme';

describe('themeOf', () => {
	it.each([
		{ name: 'a saved dark choice wins over a light system', stored: 'dark', prefersDark: false, theme: 'dark' },
		{ name: 'a saved light choice wins over a dark system', stored: 'light', prefersDark: true, theme: 'light' },
		{ name: 'a dark system decides without a choice', stored: null, prefersDark: true, theme: 'dark' },
		{ name: 'a light system decides without a choice', stored: null, prefersDark: false, theme: 'light' },
		{ name: 'an unknown saved value is ignored', stored: 'purple', prefersDark: true, theme: 'dark' }
	])('returns $theme when $name', ({ stored, prefersDark, theme }) => {
		expect(themeOf(stored, prefersDark)).toBe(theme);
	});
});

describe('toggled', () => {
	it.each([
		{ from: 'light', to: 'dark' },
		{ from: 'dark', to: 'light' }
	] as const)('flips $from to $to', ({ from, to }) => {
		expect(toggled(from)).toBe(to);
	});
});
