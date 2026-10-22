import { createServer, type RequestListener, type Server } from 'node:http';
import { csp } from '../csp.ts';
import { serveFile } from '../serve.ts';

type Name = 'data' | 'console' | 'notebook';

function listen(handler: RequestListener): Promise<{ server: Server; port: number }> {
	const server = createServer(handler);

	return new Promise((resolve, reject) => {
		server.listen(0, '127.0.0.1', () => {
			const address = server.address();

			if (address === null || typeof address === 'string') {
				reject(new Error('The server has no port'));
				return;
			}

			resolve({ server, port: address.port });
		});
	});
}

function stop({ server }: { server: Server }): Promise<void> {
	return new Promise((done) => {
		server.close(() => {
			done();
		});
	});
}

/**
 * Start the three servers of the test: the notebook site, the data, and the stub of the console.
 * The page of another origin is the page of the notebook itself, which the test opens on its own.
 */
export async function startServers({ site, data }: { site: string; data: string }) {
	const ports: Partial<Record<Name, number>> = {};
	const origin = (name: Name): string => {
		const port = ports[name];

		if (port === undefined) {
			throw new Error(`The server ${name} is not running`);
		}

		return `http://127.0.0.1:${port}`;
	};
	const sent: string[] = [];

	const dataServer = await listen((request, response) => {
		sent.push(request.url ?? '');
		serveFile(data, request, response, {
			'Access-Control-Allow-Origin': origin('notebook'),
			'Access-Control-Expose-Headers': 'Content-Length, Content-Range, ETag, Accept-Ranges'
		});
	});
	ports.data = dataServer.port;

	const consoleServer = await listen((request, response) => {
		if (request.url?.startsWith('/grant.json')) {
			const files = [{ data: `${origin('data')}/people.parquet`, delete: `${origin('data')}/people-delete.parquet` }];
			response.writeHead(200, { 'Content-Type': 'application/json' });
			response.end(
				JSON.stringify({ schema: 'sales', tables: [{ name: 'people', files }], expiresAt: Date.now() + 300_000 })
			);
			return;
		}

		response.writeHead(200, { 'Content-Type': 'text/html' });
		response.end(`<!doctype html><iframe id="nb" src="${origin('notebook')}/" sandbox="allow-scripts allow-same-origin allow-downloads allow-forms" width="800" height="600"></iframe>
<script>
window.statuses = [];
const nb = document.getElementById('nb');
window.addEventListener('message', async (event) => {
  if (event.origin !== '${origin('notebook')}' || event.source !== nb.contentWindow) return;
  window.statuses.push(event.data);
  if (event.data.type === 'ready') {
    const grant = await (await fetch('/grant.json')).json();
    nb.contentWindow.postMessage({ type: 'grant', grant }, '${origin('notebook')}');
  }
});
</script>`);
	});
	ports.console = consoleServer.port;

	const notebookServer = await listen((request, response) => {
		if (request.url === '/config.js') {
			response.writeHead(200, { 'Content-Type': 'text/javascript' });
			response.end(`globalThis.CONSOLE_ORIGIN = ${JSON.stringify(origin('console'))};`);
			return;
		}

		serveFile(site, request, response, {
			'Content-Security-Policy': csp({ consoleOrigin: origin('console'), storageOrigin: origin('data') })
		});
	});
	ports.notebook = notebookServer.port;

	return {
		origin,
		sent,
		close: () => Promise.all([dataServer, consoleServer, notebookServer].map(stop))
	};
}
