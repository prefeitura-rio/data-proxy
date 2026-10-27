export type PageQuery = { page: number; size: number };

/** Read the page number of a list from the address. A wrong value means the first page. */
export function pageOf(params: { get(name: string): string | null }, size: number, key = 'page'): PageQuery {
	const page = Number(params.get(key) ?? 0);

	return { page: Number.isInteger(page) && page > 0 ? page : 0, size };
}

export const rangeQuery = ({ page, size }: PageQuery) => `limit=${size}&offset=${page * size}`;

/** Read the total of rows from the Content-Range header of PostgREST, or null when it is unknown. */
export function parseTotal(contentRange: string | null): number | null {
	const total = /\/(\d+)$/.exec(contentRange ?? '')?.[1];

	return total === undefined ? null : Number(total);
}

export function rangeText({ page, size }: PageQuery, shown: number, total: number | null): string {
	if (shown === 0) {
		return 'No rows';
	}

	const first = page * size + 1;
	const range = `${first} to ${first + shown - 1}`;

	return total === null ? range : `${range} of ${total}`;
}

/** Build a PostgREST filter that keeps the rows where one of the columns contains the text. The characters that PostgREST reads as syntax are removed. */
export function containsFilter(columns: string[], text: string): string {
	const value = encodeURIComponent(text.replaceAll(/[*,()]/g, '').trim());

	if (value === '') {
		return '';
	}

	if (columns.length === 1) {
		return `&${columns[0] ?? ''}=ilike.*${value}*`;
	}

	return `&or=(${columns.map((column) => `${column}.ilike.*${value}*`).join(',')})`;
}

type Params = { toString(): string };

/** Change some parameters of the current address and keep the others. A change that doesn't name a page parameter of `pageKeys` resets it. */
export function paramsWith(
	current: Params,
	change: Record<string, string | undefined>,
	pageKeys: string[] = ['page']
): URLSearchParams {
	const next = new URLSearchParams(current.toString());

	for (const key of pageKeys) {
		if (!(key in change)) {
			next.delete(key);
		}
	}

	for (const [key, value] of Object.entries(change)) {
		if (value === undefined || value === '' || (pageKeys.includes(key) && value === '0')) {
			next.delete(key);
		} else {
			next.set(key, value);
		}
	}

	return next;
}

/** Build the query part of a link with `paramsWith`. */
export function linkWith(
	current: Params,
	change: Record<string, string | undefined>,
	pageKeys: string[] = ['page']
): string {
	const query = paramsWith(current, change, pageKeys).toString();

	return query === '' ? '' : `?${query}`;
}
