export type Theme = 'light' | 'dark';

/** The key of the browser storage that keeps the choice of the user. */
export const THEME_KEY = 'theme';

const isTheme = (value: string | null): value is Theme => value === 'light' || value === 'dark';

/** The theme to show: the saved choice, or else the preference of the system. */
export const themeOf = (stored: string | null, prefersDark: boolean): Theme =>
	isTheme(stored) ? stored : prefersDark ? 'dark' : 'light';

export const toggled = (theme: Theme): Theme => (theme === 'dark' ? 'light' : 'dark');

/** The theme that the page shows now, from the attribute that the toggle and the first script of the page set. */
export const currentTheme = (): Theme =>
	themeOf(document.documentElement.dataset.theme ?? null, matchMedia('(prefers-color-scheme: dark)').matches);

/** Save the choice of the user. A browser that blocks its storage keeps the choice until the page closes. */
export const saveTheme = (theme: Theme, storage: Pick<Storage, 'setItem'>): void => {
	try {
		storage.setItem(THEME_KEY, theme);
	} catch {
		return;
	}
};
