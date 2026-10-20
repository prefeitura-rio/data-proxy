import { fileURLToPath } from 'node:url';

const path = (specifier) => fileURLToPath(import.meta.resolve(specifier));

/** Plugins and the stylesheet are absolute paths, so the hook works from the repository root and from this folder. */
export default {
	useTabs: true,
	singleQuote: true,
	printWidth: 120,
	trailingComma: 'none',
	plugins: [path('prettier-plugin-svelte'), path('prettier-plugin-tailwindcss')],
	tailwindStylesheet: path('./src/app.css'),
	overrides: [{ files: '*.svelte', options: { parser: 'svelte' } }]
};
