import { describe, expect, it } from 'vitest';
import { containsFilter, linkWith, pageOf, parseTotal, rangeQuery, rangeText } from './list';

describe('pageOf', () => {
	it.each([
		['a missing page', '', 0],
		['a page number', 'page=3', 3],
		['a negative page', 'page=-2', 0],
		['text', 'page=abc', 0],
		['a fraction', 'page=1.5', 0]
	])('reads %s', (_, query, page) => {
		expect(pageOf(new URLSearchParams(query), 25)).toEqual({ page, size: 25 });
	});
});

describe('pageOf with a key', () => {
	it('reads the page of another list', () => {
		expect(pageOf(new URLSearchParams('page=4&tasks_page=2'), 50, 'tasks_page')).toEqual({ page: 2, size: 50 });
	});
});

describe('rangeQuery', () => {
	it.each([
		[{ page: 0, size: 25 }, 'limit=25&offset=0'],
		[{ page: 2, size: 25 }, 'limit=25&offset=50'],
		[{ page: 3, size: 10 }, 'limit=10&offset=30']
	])('builds the range of %j', (page, query) => {
		expect(rangeQuery(page)).toBe(query);
	});
});

describe('parseTotal', () => {
	it.each([
		['0-24/123', 123],
		['*/0', 0],
		['25-49/50', 50],
		['0-24/*', null],
		['', null],
		[null, null]
	])('reads %j', (header, total) => {
		expect(parseTotal(header)).toBe(total);
	});
});

describe('rangeText', () => {
	it.each([
		['the first page', { page: 0, size: 25 }, 25, 123, '1 to 25 of 123'],
		['a middle page', { page: 2, size: 25 }, 25, 123, '51 to 75 of 123'],
		['the last page', { page: 4, size: 25 }, 23, 123, '101 to 123 of 123'],
		['an empty list', { page: 0, size: 25 }, 0, 0, 'No rows'],
		['an unknown total', { page: 1, size: 25 }, 25, null, '26 to 50']
	])('describes %s', (_, page, shown, total, text) => {
		expect(rangeText(page, shown, total)).toBe(text);
	});
});

describe('containsFilter', () => {
	it.each([
		['no text', ['subject'], '', ''],
		['blank text', ['subject'], '   ', ''],
		['one column', ['subject'], 'ana', '&subject=ilike.*ana*'],
		['two columns', ['subject', 'unit_id'], 'ana', '&or=(subject.ilike.*ana*,unit_id.ilike.*ana*)'],
		['text with operators', ['subject'], 'a*b,(c)', '&subject=ilike.*abc*'],
		['text with spaces and accents', ['reason'], ' são paulo ', '&reason=ilike.*s%C3%A3o%20paulo*']
	])('builds the filter for %s', (_, columns, text, filter) => {
		expect(containsFilter(columns, text)).toBe(filter);
	});
});

describe('linkWith', () => {
	it.each([
		['keeps the filters and sets the page', '?status=ERROR&page=1', { page: '2' }, '?status=ERROR&page=2'],
		['drops an empty value', '?status=ERROR', { status: '' }, ''],
		['drops the page of the first page', '?page=3', { page: '0' }, ''],
		['adds a filter and resets the page', '?page=3', { status: 'ERROR', page: undefined }, '?status=ERROR']
	])('%s', (_, query, change, link) => {
		expect(linkWith(new URLSearchParams(query), change)).toBe(link);
	});

	it('keeps the page of another list when it changes the page of this one', () => {
		const link = linkWith(new URLSearchParams('page=3'), { errors_page: '1' }, ['errors_page']);

		expect(link).toBe('?page=3&errors_page=1');
	});

	it('resets the page of a list when a filter of it changes', () => {
		const link = linkWith(new URLSearchParams('errors_page=4&page=2'), { reason: 'cron' }, ['errors_page']);

		expect(link).toBe('?page=2&reason=cron');
	});
});
