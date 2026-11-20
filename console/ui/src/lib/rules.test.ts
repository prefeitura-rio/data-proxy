import { describe, expect, it } from 'vitest';
import {
	asEditor,
	describe as sentence,
	fieldsOf,
	intoAlternatives,
	intoCard,
	intoNewCard,
	parseRules,
	startWays,
	toEditor,
	toRows,
	warnings,
	WAY,
	withBlank,
	withCard,
	without,
	type Editor,
	type Rule,
	type Stored,
	type Ways
} from './rules';

const rule = (column: string, unit_type: string) => ({ column, unit_type });
const way = (...rules: ReturnType<typeof rule>[]) => ({ all: rules });

describe('toEditor', () => {
	it.each([
		['no rules', [], { any: [], all: [] }],
		[
			'plain conditions are alternatives',
			[rule('a', 'x'), rule('b', 'y')],
			{ any: [rule('a', 'x'), rule('b', 'y')], all: [] }
		],
		['a way is a card', [way(rule('a', 'x'), rule('b', 'y'))], { any: [], all: [[rule('a', 'x'), rule('b', 'y')]] }],
		['a way of one condition is an alternative', [way(rule('a', 'x'))], { any: [rule('a', 'x')], all: [] }],
		[
			'two ways make two cards, in the order that they come',
			[way(rule('c', 'z'), rule('d', 'w')), way(rule('a', 'x'), rule('b', 'y'))],
			{
				any: [],
				all: [
					[rule('c', 'z'), rule('d', 'w')],
					[rule('a', 'x'), rule('b', 'y')]
				]
			}
		],
		[
			'cards and alternatives together',
			[way(rule('a', 'x'), rule('b', 'y')), rule('c', 'z')],
			{ any: [rule('c', 'z')], all: [[rule('a', 'x'), rule('b', 'y')]] }
		]
	])('reads %s', (_name, ways, expected) => {
		expect(toEditor(ways)).toEqual(expected);
	});
});

describe('toRows', () => {
	it.each([
		['no rules', { any: [], all: [] }, []],
		[
			'alternatives are plain conditions',
			{ any: [rule('a', 'x'), rule('b', 'y')], all: [] },
			[rule('a', 'x'), rule('b', 'y')]
		],
		[
			'a card of two conditions is a way',
			{ any: [], all: [[rule('a', 'x'), rule('b', 'y')]] },
			[way(rule('a', 'x'), rule('b', 'y'))]
		],
		['a card of one condition is a plain condition', { any: [], all: [[rule('a', 'x')]] }, [rule('a', 'x')]],
		['an empty card is dropped', { any: [rule('c', 'z')], all: [[]] }, [rule('c', 'z')]],
		[
			'alternatives come before the cards, and the cards keep their order',
			{
				any: [rule('c', 'z')],
				all: [
					[rule('a', 'x'), rule('b', 'y')],
					[rule('d', 'w'), rule('e', 'v')]
				]
			},
			[rule('c', 'z'), way(rule('a', 'x'), rule('b', 'y')), way(rule('d', 'w'), rule('e', 'v'))]
		]
	])('writes %s', (_name, editor, expected) => {
		expect(toRows(editor)).toEqual(expected);
	});

	it('writes no number, whatever the editor holds', () => {
		const editor: Editor = {
			any: [rule('c', 'z')],
			all: [
				[rule('a', 'x'), rule('b', 'y')],
				[rule('d', 'w'), rule('e', 'v')]
			]
		};

		expect(JSON.stringify(toRows(editor))).not.toMatch(/group|number|"id"/);
	});

	it('round trips: reading what it wrote gives the same editor', () => {
		const editor: Editor = {
			any: [rule('c', 'z')],
			all: [
				[rule('a', 'x'), rule('b', 'y')],
				[rule('d', 'w'), rule('e', 'v')]
			]
		};

		expect(toEditor(toRows(editor))).toEqual(editor);
	});
});

describe('describe', () => {
	it.each([
		['no rules', { any: [], all: [] }, 'No rule: only a reader of every row sees a row.'],
		[
			'one rule',
			{ any: [rule('area', 'area')], all: [] },
			'A user sees a row when its area matches one of their area units.'
		],
		[
			'two alternatives',
			{ any: [rule('a', 'x'), rule('b', 'y')], all: [] },
			'A user sees a row when its a matches one of their x units, or when its b matches one of their y units.'
		],
		[
			'one card',
			{ any: [], all: [[rule('a', 'x'), rule('b', 'y')]] },
			'A user sees a row when its a matches one of their x units and its b matches one of their y units.'
		],
		[
			'a card and an alternative',
			{ any: [rule('c', 'z')], all: [[rule('a', 'x'), rule('b', 'y')]] },
			'A user sees a row when its c matches one of their z units, or when its a matches one of their x units and its b matches one of their y units.'
		],
		[
			'three conditions in a card',
			{ any: [], all: [[rule('a', 'x'), rule('b', 'y'), rule('c', 'z')]] },
			'A user sees a row when its a matches one of their x units, its b matches one of their y units and its c matches one of their z units.'
		]
	])('writes the sentence for %s', (_name, editor, expected) => {
		expect(sentence(editor)).toBe(expected);
	});
});

describe('warnings', () => {
	const mixes = (n: number, types: string) =>
		`${WAY} ${n} mixes the unit types ${types}. A user needs a grant of each type, or sees no row from it.`;
	const single = (n: number) => `${WAY} ${n} has one condition, so it works like a condition on its own.`;
	const twice = (n: number) => `${WAY} ${n} holds the same condition twice.`;

	it.each([
		['no card', { any: [rule('a', 'x')], all: [] }, []],
		['a card with one unit type', { any: [], all: [[rule('a', 'x'), rule('b', 'x')]] }, []],
		['a card that mixes unit types', { any: [], all: [[rule('a', 'x'), rule('b', 'y')]] }, [mixes(1, 'x and y')]],
		[
			'the second card mixes three types',
			{
				any: [],
				all: [
					[rule('a', 'x'), rule('e', 'x')],
					[rule('b', 'y'), rule('c', 'z'), rule('d', 'w')]
				]
			},
			[mixes(2, 'y, z and w')]
		],
		['a card of one rule', { any: [], all: [[rule('a', 'x')]] }, [single(1)]],
		['the same condition twice in a card', { any: [], all: [[rule('a', 'x'), rule('a', 'x')]] }, [twice(1)]],
		['an alternative that a card also holds', { any: [rule('a', 'x')], all: [[rule('a', 'x'), rule('b', 'x')]] }, []],
		[
			'two problems in two cards',
			{ any: [], all: [[rule('a', 'x')], [rule('b', 'x'), rule('c', 'y')]] },
			[single(1), mixes(2, 'x and y')]
		]
	])('flags %s', (_name, editor, expected) => {
		expect(warnings(editor)).toEqual(expected);
	});
});

describe('parseRules', () => {
	const form = (...entries: [string, string][]) => {
		const data = new FormData();

		for (const [name, value] of entries) {
			data.append(name, value);
		}

		return data;
	};
	const one = (column: string, unit: string, way: string): [string, string][] => [
		['rule_column', column],
		['rule_unit_type', unit],
		['rule_way', way]
	];

	it.each([
		['no rule', form(), { ok: true, rows: [] }],
		[
			'alternatives (way 0)',
			form(...one('a', 'x', '0'), ...one('b', 'y', '0')),
			{
				ok: true,
				rows: [
					{ column: 'a', unit_type: 'x' },
					{ column: 'b', unit_type: 'y' }
				]
			}
		],
		[
			'a card (way 1 twice)',
			form(...one('a', 'x', '1'), ...one('b', 'y', '1')),
			{
				ok: true,
				rows: [{ all: [rule('a', 'x'), rule('b', 'y')] }]
			}
		],
		[
			'two cards, in the order that they appear',
			form(...one('a', 'x', '2'), ...one('b', 'y', '2'), ...one('c', 'z', '1'), ...one('d', 'w', '1')),
			{
				ok: true,
				rows: [{ all: [rule('a', 'x'), rule('b', 'y')] }, { all: [rule('c', 'z'), rule('d', 'w')] }]
			}
		],
		[
			'a card of one rule becomes a plain rule',
			form(...one('a', 'x', '1')),
			{ ok: true, rows: [{ column: 'a', unit_type: 'x' }] }
		],
		[
			'blanks around a value are trimmed',
			form(...one(' a ', ' x ', '0')),
			{ ok: true, rows: [{ column: 'a', unit_type: 'x' }] }
		]
	])('reads %s', (_name, data, expected) => {
		expect(parseRules(data)).toEqual(expected);
	});

	it('turns the form positions into ways, and the result holds no number', () => {
		const data = form(
			...one('a', 'x', '7'),
			...one('b', 'y', '7'),
			...one('c', 'z', '0'),
			...one('d', 'w', '3'),
			...one('e', 'v', '3')
		);
		const parsed = parseRules(data);

		expect(parsed).toEqual({
			ok: true,
			rows: [rule('c', 'z'), { all: [rule('a', 'x'), rule('b', 'y')] }, { all: [rule('d', 'w'), rule('e', 'v')] }]
		});
		expect(JSON.stringify(parsed)).not.toMatch(/7|3|group|way/);
	});

	it.each([
		['an empty column', form(...one('', 'x', '0')), 'Fill the column and the unit type of every condition.'],
		['an empty unit type', form(...one('a', '', '0')), 'Fill the column and the unit type of every condition.'],
		['a way that is not a number', form(...one('a', 'x', 'one')), 'A condition has a wrong way.'],
		['a negative way', form(...one('a', 'x', '-1')), 'A condition has a wrong way.'],
		[
			'the same rule twice',
			form(...one('a', 'x', '0'), ...one('a', 'x', '1')),
			'The rule a with the unit type x appears twice.'
		],
		[
			'fields that do not line up',
			form(['rule_column', 'a'], ['rule_unit_type', 'x']),
			'The rules are incomplete. Reload the page and try again.'
		]
	])('refuses %s', (_name, data, message) => {
		expect(parseRules(data)).toEqual({ ok: false, message });
	});
});

describe('the state of the editor', () => {
	const counter = () => {
		let next = 0;

		return () => `id${(next += 1)}`;
	};
	const rows = [rule('c', 'z'), way(rule('a', 'x'), rule('b', 'y'))];
	const shape = (ways: Ways) => ({
		any: ways.any.map((entry) => entry.column),
		cards: ways.cards.map((card) => card.items.map((entry) => entry.column))
	});
	const start = () => startWays(rows, counter());

	it('starts from the stored rows', () => {
		expect(shape(start())).toEqual({ any: ['c'], cards: [['a', 'b']] });
	});

	it('gives each condition and each card an id of its own', () => {
		const ways = start();
		const ids = [
			...ways.any.map((e) => e.id),
			...ways.cards.map((c) => c.id),
			...ways.cards.flatMap((c) => c.items.map((e) => e.id))
		];

		expect(new Set(ids).size).toBe(ids.length);
		expect(ways.cards[0]?.items.every((entry) => entry.card === ways.cards[0]?.id)).toBe(true);
	});

	it('gives the editor shape and the fields in the order of the form', () => {
		const ways = start();

		const editor = asEditor(ways);
		const plain = (rules: Rule[]) => rules.map(({ column, unit_type }) => ({ column, unit_type }));

		expect({ any: plain(editor.any), all: editor.all.map(plain) }).toEqual({
			any: [rule('c', 'z')],
			all: [[rule('a', 'x'), rule('b', 'y')]]
		});
		expect(fieldsOf(ways).map(({ entry, way }) => [entry.column, way])).toEqual([
			['c', 0],
			['a', 1],
			['b', 1]
		]);
	});

	it.each([
		['an alternative', 'id1', { any: [], cards: [['a', 'b']] }],
		['a condition of a card', 'id3', { any: ['c'], cards: [['b']] }]
	])('removes %s', (_name, id, expected) => {
		expect(shape(without(start(), id))).toEqual(expected);
	});

	it('drops a card that has no condition left', () => {
		const ways = without(without(start(), 'id3'), 'id4');

		expect(shape(ways)).toEqual({ any: ['c'], cards: [] });
	});

	it('adds a blank condition to the alternatives and to a card', () => {
		const ways = withBlank(withBlank(start(), 'n1', null), 'n2', 'id2');

		expect(shape(ways)).toEqual({ any: ['c', ''], cards: [['a', 'b', '']] });
	});

	it('adds a card with two blank conditions', () => {
		expect(shape(withCard(start(), 'k', 'n1', 'n2'))).toEqual({
			any: ['c'],
			cards: [
				['a', 'b'],
				['', '']
			]
		});
	});

	it('moves an alternative into a card, and a card condition to the alternatives', () => {
		expect(shape(intoCard(start(), 'id1', 'id2'))).toEqual({ any: [], cards: [['a', 'b', 'c']] });
		expect(shape(intoAlternatives(start(), 'id3'))).toEqual({ any: ['c', 'a'], cards: [['b']] });
	});

	it('keeps the state when the condition or the card does not exist', () => {
		const ways = start();

		expect(intoCard(ways, 'nope', 'id2')).toBe(ways);
		expect(intoCard(ways, 'id1', 'nope')).toBe(ways);
		expect(intoAlternatives(ways, 'id1')).toBe(ways);
		expect(intoNewCard(ways, 'nope', 'k', 'n')).toBe(ways);
	});

	it('moves a condition into a new card with a blank one beside it', () => {
		expect(shape(intoNewCard(start(), 'id1', 'k', 'n1'))).toEqual({
			any: [],
			cards: [
				['a', 'b'],
				['c', '']
			]
		});
	});

	it('makes the same ways after a move and a move back', () => {
		const ways = intoAlternatives(intoCard(start(), 'id1', 'id2'), 'id1');
		const text = (stored: Stored[]) => stored.map((way) => JSON.stringify(way)).sort();

		expect(text(toRows(asEditor(ways)))).toEqual(text(toRows(asEditor(start()))));
	});
});
