type Origins = { consoleOrigin: string; storageOrigin: string };

/**
 * The Content Security Policy of the notebook origin. It allows only the files of the site and the storage host.
 * The chart builds the same string for the web server.
 */
export function csp({ consoleOrigin, storageOrigin }: Origins): string {
	return [
		"default-src 'self'",
		"script-src 'self' 'unsafe-eval' 'unsafe-inline' 'wasm-unsafe-eval' blob:",
		"worker-src 'self' blob:",
		`connect-src 'self' blob: data: ${storageOrigin}`,
		"style-src 'self' 'unsafe-inline'",
		"img-src 'self' data: blob:",
		"font-src 'self' data:",
		`frame-ancestors ${consoleOrigin}`
	].join('; ');
}
