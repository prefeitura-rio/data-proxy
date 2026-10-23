import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true)
			},
			adapter: adapter(),
			// The hook in src/hooks.server.ts checks the origin against ORIGIN. The built-in check compares with the
			// request address, which is http behind the gateway.
			csrf: { trustedOrigins: ['*'] }
		})
	],
	test: { include: ['src/**/*.test.ts'] }
});
