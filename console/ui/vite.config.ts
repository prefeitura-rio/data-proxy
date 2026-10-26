import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';
import { BASE } from './src/lib/base.ts';

export default defineConfig({
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true)
			},
			adapter: adapter(),
			// The console answers on this path of the host of the API, so the gateway can send one path to it. Absolute paths
			// let the server use resolve() for redirects and cookies.
			paths: { base: BASE, relative: false },
			// The hook in src/hooks.server.ts checks the origin against ORIGIN. The built-in check compares with the
			// request address, which is http behind the gateway.
			csrf: { trustedOrigins: ['*'] }
		})
	],
	test: {
		include: ['src/**/*.test.ts'],
		coverage: { provider: 'v8', include: ['src/lib/**/*.ts'], exclude: ['src/**/*.test.ts'], reporter: ['text'] }
	}
});
