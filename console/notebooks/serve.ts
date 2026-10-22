import { createReadStream, type Stats, statSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { extname, join, normalize } from 'node:path';

const TYPES: Record<string, string> = {
	'.html': 'text/html',
	'.js': 'text/javascript',
	'.mjs': 'text/javascript',
	'.wasm': 'application/wasm',
	'.json': 'application/json',
	'.css': 'text/css',
	'.parquet': 'application/octet-stream',
	'.svg': 'image/svg+xml',
	'.png': 'image/png',
	'.ico': 'image/x-icon',
	'.woff': 'font/woff',
	'.woff2': 'font/woff2',
	'.webmanifest': 'application/manifest+json',
	'.txt': 'text/plain',
	'.map': 'application/json',
	'.zip': 'application/zip'
};

function statOrNull(path: string): Stats | null {
	try {
		return statSync(path);
	} catch {
		return null;
	}
}

/**
 * Serve one file of a folder, with Range requests, because DuckDB-WASM reads Parquet files by range.
 */
export function serveFile(
	root: string,
	request: IncomingMessage,
	response: ServerResponse,
	headers: Record<string, string> = {}
): void {
	const url = new URL(request.url ?? '/', 'http://localhost');
	const path = normalize(join(root, decodeURIComponent(url.pathname))).replace(/\/$/, '/index.html');
	const stat = path.startsWith(normalize(root)) ? statOrNull(path) : null;

	if (stat === null) {
		response.writeHead(404, headers).end();
		return;
	}

	const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.range ?? '');
	const start = range?.[1] ? Number(range[1]) : 0;
	const end = range?.[2] ? Math.min(Number(range[2]), stat.size - 1) : stat.size - 1;
	const type = TYPES[extname(path)] ?? 'application/octet-stream';
	const common = { 'Content-Type': type, 'Accept-Ranges': 'bytes', ...headers };

	if (range) {
		response.writeHead(206, {
			...common,
			'Content-Range': `bytes ${start}-${end}/${stat.size}`,
			'Content-Length': end - start + 1
		});
	} else {
		response.writeHead(200, { ...common, 'Content-Length': stat.size });
	}

	if (request.method === 'HEAD') {
		response.end();
		return;
	}

	createReadStream(path, { start, end }).pipe(response);
}
