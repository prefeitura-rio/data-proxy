import { describe, expect, it } from 'vitest';
import {
	asEditor,
	describe as sentence,
	explain,
	listFields,
	intoAlternatives,
	intoCard,
	intoNewCard,
	parseRules,
	startGroups,
	toEditor,
	toRows,
	warnings,
	GROUP,
	withBlank,
	withCard,
	without,
	type Editor,
	type Rule,
	type Stored,
	type Groups
} from './rules';

const rule = (column: string, unit_type: string) => ({ column, unit_type });
const group = (...rules: ReturnType<typeof rule>[]) => ({ conditions: rules });
const lone = (column: string, unit_type: string) => group(rule(column, unit_type));

describe('toEditor', () => {
	it.each([
		['no rules', [], { any: [], cards: [] }],
		[
			'rules of one condition are alternatives',
			[lone('a', 'x'), lone('b', 'y')],
			{ any: [rule('a', 'x'), rule('b', 'y')], cards: [] }
		],
		[
			'a group is a card',
			[group(rule('a', 'x'), rule('b', 'y'))],
			{ any: [], cards: [[rule('a', 'x'), rule('b', 'y')]] }
		],
		['a rule of one condition is an alternative', [group(rule('a', 'x'))], { any: [rule('a', 'x')], cards: [] }],
		[
			'two groups make two cards, in the order that they come',
			[group(rule('c', 'z'), rule('d', 'w')), group(rule('a', 'x'), rule('b', 'y'))],
			{
				any: [],
				cards: [
					[rule('c', 'z'), rule('d', 'w')],
					[rule('a', 'x'), rule('b', 'y')]
				]
			}
		],
		[
			'cards and alternatives together',
			[group(rule('a', 'x'), rule('b', 'y')), lone('c', 'z')],
			{ any: [rule('c', 'z')], cards: [[rule('a', 'x'), rule('b', 'y')]] }
		]
	])('reads %s', (_name, groups, expected) => {
		expect(toEditor(groups)).toEqual(expected);
	});
});

describe('toRows', () => {
	it.each([
		['no rules', { any: [], cards: [] }, []],
		[
			'alternatives are rules of one condition',
			{ any: [rule('a', 'x'), rule('b', 'y')], cards: [] },
			[lone('a', 'x'), lone('b', 'y')]
		],
		[
			'a card of two conditions is a group',
			{ any: [], cards: [[rule('a', 'x'), rule('b', 'y')]] },
			[group(rule('a', 'x'), rule('b', 'y'))]
		],
		['a card of one condition is a rule of one condition', { any: [], cards: [[rule('a', 'x')]] }, [lone('a', 'x')]],
		['an empty card is dropped', { any: [rule('c', 'z')], cards: [[]] }, [lone('c', 'z')]],
		[
			'alternatives come before the cards, and the cards keep their order',
			{
				any: [rule('c', 'z')],
				cards: [
					[rule('a', 'x'), rule('b', 'y')],
					[rule('d', 'w'), rule('e', 'v')]
				]
			},
			[lone('c', 'z'), group(rule('a', 'x'), rule('b', 'y')), group(rule('d', 'w'), rule('e', 'v'))]
		]
	])('writes %s', (_name, editor, expected) => {
		expect(toRows(editor)).toEqual(expected);
	});

	it('writes no number, whatever the editor holds', () => {
		const editor: Editor = {
			any: [rule('c', 'z')],
			cards: [
				[rule('a', 'x'), rule('b', 'y')],
				[rule('d', 'w'), rule('e', 'v')]
			]
		};

		expect(JSON.stringify(toRows(editor))).not.toMatch(/group|number|"id"/);
	});

	it('round trips: reading what it wrote gives the same editor', () => {
		const editor: Editor = {
			any: [rule('c', 'z')],
			cards: [
				[rule('a', 'x'), rule('b', 'y')],
				[rule('d', 'w'), rule('e', 'v')]
			]
		};

		expect(toEditor(toRows(editor))).toEqual(editor);
	});
});

describe('describe', () => {
	it.each([
		['no rules', { any: [], cards: [] }, 'No rule: only a reader of every row sees a row.'],
		[
			'one rule',
			{ any: [rule('area', 'area')], cards: [] },
			'A user sees a row when its area matches one of their area units.'
		],
		[
			'two alternatives',
			{ any: [rule('a', 'x'), rule('b', 'y')], cards: [] },
			'A user sees a row when its a matches one of their x units, or when its b matches one of their y units.'
		],
		[
			'one card',
			{ any: [], cards: [[rule('a', 'x'), rule('b', 'y')]] },
			'A user sees a row when its a matches one of their x units and its b matches one of their y units.'
		],
		[
			'a card and an alternative',
			{ any: [rule('c', 'z')], cards: [[rule('a', 'x'), rule('b', 'y')]] },
			'A user sees a row when its c matches one of their z units, or when its a matches one of their x units and its b matches one of their y units.'
		],
		[
			'three conditions in a card',
			{ any: [], cards: [[rule('a', 'x'), rule('b', 'y'), rule('c', 'z')]] },
			'A user sees a row when its a matches one of their x units, its b matches one of their y units and its c matches one of their z units.'
		]
	])('writes the sentence for %s', (_name, editor, expected) => {
		expect(sentence(editor)).toBe(expected);
	});
});

describe('warnings', () => {
	const mixes = (n: number, types: string) =>
		`${GROUP} ${n} mixes the unit types ${types}. A user needs a grant of each type, or sees no row from it.`;
	const single = (n: number) => `${GROUP} ${n} has one condition, so it works like a condition on its own.`;
	const twice = (n: number) => `${GROUP} ${n} holds the same condition twice.`;

	it.each([
		['no card', { any: [rule('a', 'x')], cards: [] }, []],
		['a card with one unit type', { any: [], cards: [[rule('a', 'x'), rule('b', 'x')]] }, []],
		['a card that mixes unit types', { any: [], cards: [[rule('a', 'x'), rule('b', 'y')]] }, [mixes(1, 'x and y')]],
		[
			'the second card mixes three types',
			{
				any: [],
				cards: [
					[rule('a', 'x'), rule('e', 'x')],
					[rule('b', 'y'), rule('c', 'z'), rule('d', 'w')]
				]
			},
			[mixes(2, 'y, z and w')]
		],
		['a card of one rule', { any: [], cards: [[rule('a', 'x')]] }, [single(1)]],
		['the same condition twice in a card', { any: [], cards: [[rule('a', 'x'), rule('a', 'x')]] }, [twice(1)]],
		['an alternative that a card also holds', { any: [rule('a', 'x')], cards: [[rule('a', 'x'), rule('b', 'x')]] }, []],
		[
			'two problems in two cards',
			{ any: [], cards: [[rule('a', 'x')], [rule('b', 'x'), rule('c', 'y')]] },
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
	const one = (column: string, unit: string, group: string): [string, string][] => [
		['rule_column', column],
		['rule_unit_type', unit],
		['rule_group', group]
	];

	it.each([
		['no rule', form(), { ok: true, rows: [] }],
		[
			'alternatives (group 0)',
			form(...one('a', 'x', '0'), ...one('b', 'y', '0')),
			{
				ok: true,
				rows: [lone('a', 'x'), lone('b', 'y')]
			}
		],
		[
			'a card (group 1 twice)',
			form(...one('a', 'x', '1'), ...one('b', 'y', '1')),
			{
				ok: true,
				rows: [group(rule('a', 'x'), rule('b', 'y'))]
			}
		],
		[
			'two cards, in the order that they appear',
			form(...one('a', 'x', '2'), ...one('b', 'y', '2'), ...one('c', 'z', '1'), ...one('d', 'w', '1')),
			{
				ok: true,
				rows: [group(rule('a', 'x'), rule('b', 'y')), group(rule('c', 'z'), rule('d', 'w'))]
			}
		],
		['a card of one rule becomes a plain rule', form(...one('a', 'x', '1')), { ok: true, rows: [lone('a', 'x')] }],
		['blanks around a value are trimmed', form(...one(' a ', ' x ', '0')), { ok: true, rows: [lone('a', 'x')] }]
	])('reads %s', (_name, data, expected) => {
		expect(parseRules(data)).toEqual(expected);
	});

	it('turns the form positions into rules, and the result holds no number', () => {
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
			rows: [lone('c', 'z'), group(rule('a', 'x'), rule('b', 'y')), group(rule('d', 'w'), rule('e', 'v'))]
		});
		expect(JSON.stringify(parsed)).not.toMatch(/7|3|group/);
	});

	it.each([
		['an empty column', form(...one('', 'x', '0')), 'Fill the column and the unit type of every condition.'],
		['an empty unit type', form(...one('a', '', '0')), 'Fill the column and the unit type of every condition.'],
		['a group that is not a number', form(...one('a', 'x', 'one')), 'A condition has a wrong group.'],
		['a negative group', form(...one('a', 'x', '-1')), 'A condition has a wrong group.'],
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
	const rows = [lone('c', 'z'), group(rule('a', 'x'), rule('b', 'y'))];
	const shape = (groups: Groups) => ({
		any: groups.any.map((entry) => entry.column),
		cards: groups.cards.map((card) => card.items.map((entry) => entry.column))
	});
	const start = () => startGroups(rows, counter());

	it('starts from the stored rows', () => {
		expect(shape(start())).toEqual({ any: ['c'], cards: [['a', 'b']] });
	});

	it('gives each condition and each card an id of its own', () => {
		const groups = start();
		const ids = [
			...groups.any.map((e) => e.id),
			...groups.cards.map((c) => c.id),
			...groups.cards.flatMap((c) => c.items.map((e) => e.id))
		];

		expect(new Set(ids).size).toBe(ids.length);
		expect(groups.cards[0]?.items.every((entry) => entry.card === groups.cards[0]?.id)).toBe(true);
	});

	it('gives the editor shape and the fields in the order of the form', () => {
		const groups = start();

		const editor = asEditor(groups);
		const plain = (rules: Rule[]) => rules.map(({ column, unit_type }) => ({ column, unit_type }));

		expect({ any: plain(editor.any), cards: editor.cards.map(plain) }).toEqual({
			any: [rule('c', 'z')],
			cards: [[rule('a', 'x'), rule('b', 'y')]]
		});
		expect(listFields(groups).map(({ entry, group }) => [entry.column, group])).toEqual([
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
		const groups = without(without(start(), 'id3'), 'id4');

		expect(shape(groups)).toEqual({ any: ['c'], cards: [] });
	});

	it('adds a blank condition to the alternatives and to a card', () => {
		const groups = withBlank(withBlank(start(), 'n1', null), 'n2', 'id2');

		expect(shape(groups)).toEqual({ any: ['c', ''], cards: [['a', 'b', '']] });
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
		const groups = start();

		expect(intoCard(groups, 'nope', 'id2')).toBe(groups);
		expect(intoCard(groups, 'id1', 'nope')).toBe(groups);
		expect(intoAlternatives(groups, 'id1')).toBe(groups);
		expect(intoNewCard(groups, 'nope', 'k', 'n')).toBe(groups);
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

	it('makes the same groups after a move and a move back', () => {
		const groups = intoAlternatives(intoCard(start(), 'id1', 'id2'), 'id1');
		const text = (stored: Stored[]) => stored.map((group) => JSON.stringify(group)).sort();

		expect(text(toRows(asEditor(groups)))).toEqual(text(toRows(asEditor(start()))));
	});
});

describe('explain', () => {
	const grants = (...pairs: [string, string][]) => pairs.map(([unit_type, unit_id]) => ({ unit_type, unit_id }));
	const found = (column: string, unit_type: string, values: string[]) => ({ column, unit_type, values });

	it.each([
		[
			'no rule',
			{ any: [], cards: [] },
			grants(['x', '1']),
			{ everything: false, any: [], cards: [], rows: 'No rule, so the subject sees no row.' }
		],
		[
			'an alternative with grants',
			{ any: [rule('a', 'x')], cards: [] },
			grants(['x', '1'], ['x', '2'], ['y', '9']),
			{
				everything: false,
				any: [found('a', 'x', ['1', '2'])],
				cards: [],
				rows: 'The subject sees a row when its a is 1 or 2.'
			}
		],
		[
			'an alternative with no grant',
			{ any: [rule('a', 'x')], cards: [] },
			grants(['y', '9']),
			{
				everything: false,
				any: [found('a', 'x', [])],
				cards: [],
				rows: 'The subject sees no row, because no condition has a grant.'
			}
		],
		[
			'a card with every grant',
			{ any: [], cards: [[rule('a', 'x'), rule('b', 'y')]] },
			grants(['x', '1'], ['y', '2']),
			{
				everything: false,
				any: [],
				cards: [{ active: true, missing: [], conditions: [found('a', 'x', ['1']), found('b', 'y', ['2'])] }],
				rows: 'The subject sees a row when its a is 1 and its b is 2.'
			}
		],
		[
			'a card with a missing grant drops out',
			{ any: [], cards: [[rule('a', 'x'), rule('b', 'y')]] },
			grants(['x', '1']),
			{
				everything: false,
				any: [],
				cards: [{ active: false, missing: ['y'], conditions: [found('a', 'x', ['1']), found('b', 'y', [])] }],
				rows: 'The subject sees no row, because no condition has a grant.'
			}
		],
		[
			'a dropped card beside a kept alternative',
			{ any: [rule('c', 'z')], cards: [[rule('a', 'x'), rule('b', 'y')]] },
			grants(['x', '1'], ['z', '3']),
			{
				everything: false,
				any: [found('c', 'z', ['3'])],
				cards: [{ active: false, missing: ['y'], conditions: [found('a', 'x', ['1']), found('b', 'y', [])] }],
				rows: 'The subject sees a row when its c is 3.'
			}
		],
		[
			'two kept groups',
			{ any: [rule('c', 'z')], cards: [[rule('a', 'x'), rule('b', 'y')]] },
			grants(['x', '1'], ['y', '2'], ['z', '3']),
			{
				everything: false,
				any: [found('c', 'z', ['3'])],
				cards: [{ active: true, missing: [], conditions: [found('a', 'x', ['1']), found('b', 'y', ['2'])] }],
				rows: 'The subject sees a row when its c is 3, or when its a is 1 and its b is 2.'
			}
		],
		[
			'the wildcard grant',
			{ any: [rule('a', 'x')], cards: [] },
			grants(['*', '*']),
			{
				everything: true,
				any: [found('a', 'x', [])],
				cards: [],
				rows: 'The subject sees every row, because it holds the grant * on * .'
			}
		],
		[
			'a repeated grant value is shown once',
			{ any: [rule('a', 'x')], cards: [] },
			grants(['x', '1'], ['x', '1']),
			{
				everything: false,
				any: [found('a', 'x', ['1'])],
				cards: [],
				rows: 'The subject sees a row when its a is 1.'
			}
		]
	])('explains %s', (_name, editor, held, expected) => {
		expect(explain(editor, held)).toEqual(expected);
	});
});
