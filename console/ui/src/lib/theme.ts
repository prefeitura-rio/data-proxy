export type Theme = 'light' | 'dark';

/** The key of the browser storage that keeps the choice of the user. */
export const THEME_KEY = 'theme';

const isTheme = (value: string | null): value is Theme => value === 'light' || value === 'dark';

/** The theme to show: the saved choice, or else the preference of the system. */
export const themeOf = (stored: string | null, prefersDark: boolean): Theme =>
	isTheme(stored) ? stored : prefersDark ? 'dark' : 'light';

export const toggled = (theme: Theme): Theme => (theme === 'dark' ? 'light' : 'dark');
