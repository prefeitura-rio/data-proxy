import { readFile } from 'node:fs/promises';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { join } from 'node:path';
import { serveFile } from '../serve.ts';

/** The text that the template cell of the notebook has where a table name goes. */
const PLACEHOLDER = '{dataset}';

/** A table name: letters, digits, underscore and hyphen. A name like this can't close a string or add code. */
const NAME = /^[\p{L}\p{N}_-]+$/u;

const CODE = /(<marimo-code hidden="">)([^<]*)(<\/marimo-code>)/;

/** marimo writes a blank pair of lines between two top-level blocks. */
const SEPARATOR = '\n\n\n';

export const tablesOf = (url: URL): string[] =>
	[...new Set(url.searchParams.getAll('tables'))].filter((name) => NAME.test(name));

/**
 * Write one cell for each table in the code of the exported notebook. The notebook has one template cell that holds
 * the placeholder. The cell comes back once for each valid table name, and a notebook with no table has none.
 */
export function withTables(html: string, tables: string[]): string {
	const match = CODE.exec(html);

	if (match === null) {
		return html;
	}

	const blocks = decodeURIComponent(match[2] ?? '').split(SEPARATOR);
	const index = blocks.findIndex((block) => block.includes(PLACEHOLDER));
	const template = blocks[index];

	if (template === undefined) {
		return html;
	}

	const cells = tables.filter((name) => NAME.test(name)).map((name) => template.replaceAll(PLACEHOLDER, () => name));
	const code = blocks.toSpliced(index, 1, ...cells).join(SEPARATOR);

	return html.replace(
		CODE,
		(_all, open: string, _old: string, close: string) => `${open}${encodeURIComponent(code)}${close}`
	);
}

/** Serve the notebook site. The page of the notebook gets the cells of the tables in the address, and every other file is served as it is. */
export async function servePage(
	root: string,
	request: IncomingMessage,
	response: ServerResponse,
	headers: Record<string, string> = {}
): Promise<void> {
	const url = new URL(request.url ?? '/', 'http://localhost');

	if (url.pathname !== '/' && url.pathname !== '/index.html') {
		serveFile(root, request, response, headers);
		return;
	}

	const html = await readFile(join(root, 'index.html'), 'utf8').catch(() => null);

	if (html === null) {
		response.writeHead(404, headers).end();
		return;
	}

	const body = withTables(html, tablesOf(url));

	response.writeHead(200, { 'Content-Type': 'text/html', 'Content-Length': Buffer.byteLength(body), ...headers });
	response.end(request.method === 'HEAD' ? undefined : body);
}
