// The web server of the notebook site. The console image runs it with `node notebooks/server.ts`.
// It sends the Content Security Policy from CSP, or the one that csp.ts builds from the origins of the console and
// the storage. It tells the page the origin of the console in config.js.
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { csp } from './csp.ts';
import { servePage } from './src/page.ts';

const { CSP, CONSOLE_ORIGIN, STORAGE_ORIGIN, PORT = '8080' } = process.env;

if (!CONSOLE_ORIGIN || !(CSP || STORAGE_ORIGIN)) {
	throw new Error('CONSOLE_ORIGIN, and CSP or STORAGE_ORIGIN, are required');
}

const policy = CSP ?? csp({ consoleOrigin: CONSOLE_ORIGIN, storageOrigin: STORAGE_ORIGIN ?? '' });

const root = fileURLToPath(new URL('./site', import.meta.url));
const headers = {
	'Content-Security-Policy': policy,
	'X-Content-Type-Options': 'nosniff',
	'Referrer-Policy': 'no-referrer'
};

createServer((request, response) => {
	if (request.url === '/config.js') {
		response.writeHead(200, { ...headers, 'Content-Type': 'text/javascript' });
		response.end(`globalThis.CONSOLE_ORIGIN = ${JSON.stringify(CONSOLE_ORIGIN)};`);
		return;
	}

	void servePage(root, request, response, headers);
}).listen(Number(PORT));

// Node as the first process of a container ignores SIGTERM unless the program handles it.
process.on('SIGTERM', () => process.exit(0));
