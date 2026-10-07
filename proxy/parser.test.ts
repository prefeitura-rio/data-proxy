import { test } from "node:test";
import assert from "node:assert/strict";

import generatedParser from "./parser.ts";

const { parseFilter } = generatedParser;

test("parses comparison filters into the filter AST", () => {
    assert.deepEqual(parseFilter("id=eq.42"), {
        where: {
            kind: "comparison",
            column: "id",
            operator: "eq",
            negated: false,
            value: "42",
        },
    });
});

test("parses nested logical filters without producing SQL", () => {
    assert.deepEqual(
        parseFilter("or=(and(id.gte.10,id.lt.20),name.ilike.*rio*)"),
        {
            where: {
                kind: "group",
                operator: "or",
                items: [
                    {
                        kind: "group",
                        operator: "and",
                        items: [
                            {
                                kind: "comparison",
                                column: "id",
                                operator: "gte",
                                negated: false,
                                value: "10",
                            },
                            {
                                kind: "comparison",
                                column: "id",
                                operator: "lt",
                                negated: false,
                                value: "20",
                            },
                        ],
                    },
                    {
                        kind: "comparison",
                        column: "name",
                        operator: "ilike",
                        negated: false,
                        value: "*rio*",
                    },
                ],
            },
        },
    );
});

test("parses quoted in-list values", () => {
    assert.deepEqual(parseFilter('name=in.("Rio, RJ","O\\"Connor",plain)'), {
        where: {
            kind: "comparison",
            column: "name",
            operator: "in",
            negated: false,
            value: ["Rio, RJ", 'O"Connor', "plain"],
        },
    });
});

test("parses is values and negated operators", () => {
    assert.deepEqual(parseFilter("deleted=is.not_null&name=not.ilike.*test*"), {
        where: {
            kind: "group",
            operator: "and",
            items: [
                {
                    kind: "comparison",
                    column: "deleted",
                    operator: "is",
                    negated: false,
                    value: "not_null",
                },
                {
                    kind: "comparison",
                    column: "name",
                    operator: "ilike",
                    negated: true,
                    value: "*test*",
                },
            ],
        },
    });
});

test("parses negated logical groups", () => {
    assert.deepEqual(parseFilter("not.and=(id.gte.1,id.lte.4)"), {
        where: {
            kind: "group",
            operator: "and",
            negated: true,
            items: [
                {
                    kind: "comparison",
                    column: "id",
                    operator: "gte",
                    negated: false,
                    value: "1",
                },
                {
                    kind: "comparison",
                    column: "id",
                    operator: "lte",
                    negated: false,
                    value: "4",
                },
            ],
        },
    });
});

test("parses quoted scalar values with reserved characters", () => {
    assert.deepEqual(parseFilter('name=eq."Rio, RJ"'), {
        where: {
            kind: "comparison",
            column: "name",
            operator: "eq",
            negated: false,
            value: "Rio, RJ",
        },
    });
});

test("leaves non-where parameters to PostgREST", () => {
    assert.deepEqual(
        parseFilter("id=gte.10&order=id.desc&limit=25&offset=5&select=id,name"),
        {
            where: {
                kind: "comparison",
                column: "id",
                operator: "gte",
                negated: false,
                value: "10",
            },
        },
    );
});

test("decodes URL values exactly once", () => {
    assert.deepEqual(parseFilter("name=eq.Rio%20de%20Janeiro"), {
        where: {
            kind: "comparison",
            column: "name",
            operator: "eq",
            negated: false,
            value: "Rio de Janeiro",
        },
    });
});

test("omits unsupported independent filters but keeps supported filters", () => {
    assert.deepEqual(parseFilter("id=eq.42&name=fts.search&select=id,name"), {
        where: {
            kind: "comparison",
            column: "id",
            operator: "eq",
            negated: false,
            value: "42",
        },
    });
});

test("omits a complete logical group when one child is unsupported", () => {
    assert.deepEqual(
        parseFilter("id=eq.42&or=(name.eq.rio,description.fts.city)"),
        {
            where: {
                kind: "comparison",
                column: "id",
                operator: "eq",
                negated: false,
                value: "42",
            },
        },
    );
});

test("returns no document for malformed supported syntax", () => {
    assert.equal(parseFilter("id=eq"), undefined);
    assert.equal(parseFilter("id=in.(one,,two)"), undefined);
    assert.equal(parseFilter("id=in.one"), undefined);
    assert.equal(parseFilter("id=eq.(one)"), undefined);
    assert.equal(parseFilter("id=is.maybe"), undefined);
    assert.equal(parseFilter("id=eq.%ZZ"), undefined);
    assert.equal(parseFilter("and=id.eq.1"), undefined);
    assert.equal(parseFilter("id=in.(1,2,)"), undefined);
    assert.equal(parseFilter("id=in.()"), undefined);
    assert.equal(parseFilter("and=()"), undefined);
    assert.equal(parseFilter('name=eq."abc'), undefined);
});

test("returns no document for unsupported or empty values", () => {
    assert.equal(parseFilter("id="), undefined);
    assert.equal(parseFilter("or="), undefined);
    assert.equal(parseFilter("id=EQ.1"), undefined);
});

test("ignores empty and malformed independent parameters", () => {
    assert.deepEqual(parseFilter("id=eq.1&&junk&id%2Dbad=eq.2"), {
        where: {
            kind: "comparison",
            column: "id",
            operator: "eq",
            negated: false,
            value: "1",
        },
    });
});

test("omits predicates with invalid column names", () => {
    assert.deepEqual(parseFilter("bad-key=eq.1&id=eq.2"), {
        where: {
            kind: "comparison",
            column: "id",
            operator: "eq",
            negated: false,
            value: "2",
        },
    });
});

test("parses every scalar comparison operator", () => {
    ["eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike"].forEach((operator) => {
        assert.deepEqual(parseFilter(`id=${operator}.1`), {
            where: { kind: "comparison", column: "id", operator, negated: false, value: "1" },
        });
    });
});

test("parses every is value", () => {
    ["null", "true", "false", "unknown", "not_null"].forEach((value) => {
        assert.deepEqual(parseFilter(`active=is.${value}`), {
            where: { kind: "comparison", column: "active", operator: "is", negated: false, value },
        });
    });
});

test("parses negated comparison operators", () => {
    const cases: [string, string, string][] = [
        ["not.eq.1", "eq", "1"],
        ["not.gt.1", "gt", "1"],
        ["not.like.*a*", "like", "*a*"],
        ["not.is.null", "is", "null"],
    ];
    cases.forEach(([query, operator, value]) => {
        assert.deepEqual(parseFilter(`id=${query}`), {
            where: { kind: "comparison", column: "id", operator, negated: true, value },
        });
    });
});

test("parses negated or groups", () => {
    assert.deepEqual(parseFilter("not.or=(id.gt.1,id.lt.2)"), {
        where: {
            kind: "group",
            operator: "or",
            negated: true,
            items: [
                { kind: "comparison", column: "id", operator: "gt", negated: false, value: "1" },
                { kind: "comparison", column: "id", operator: "lt", negated: false, value: "2" },
            ],
        },
    });
});

test("combines repeated filters on one column", () => {
    assert.deepEqual(parseFilter("id=gt.1&id=lt.10"), {
        where: {
            kind: "group",
            operator: "and",
            items: [
                { kind: "comparison", column: "id", operator: "gt", negated: false, value: "1" },
                { kind: "comparison", column: "id", operator: "lt", negated: false, value: "10" },
            ],
        },
    });
});

test("parses a single-child logical group", () => {
    assert.deepEqual(parseFilter("or=(id.eq.1)"), {
        where: {
            kind: "group",
            operator: "or",
            items: [
                { kind: "comparison", column: "id", operator: "eq", negated: false, value: "1" },
            ],
        },
    });
});

test("parses a negated group nested inside a group", () => {
    assert.deepEqual(parseFilter("or=(not.and(id.gt.1),id.eq.2)"), {
        where: {
            kind: "group",
            operator: "or",
            items: [
                {
                    kind: "group",
                    operator: "and",
                    negated: true,
                    items: [
                        { kind: "comparison", column: "id", operator: "gt", negated: false, value: "1" },
                    ],
                },
                { kind: "comparison", column: "id", operator: "eq", negated: false, value: "2" },
            ],
        },
    });
});

test("parses quoted-only in lists and empty members", () => {
    assert.deepEqual(parseFilter('name=in.("a","b")'), {
        where: { kind: "comparison", column: "name", operator: "in", negated: false, value: ["a", "b"] },
    });
    assert.deepEqual(parseFilter('name=in.("")'), {
        where: { kind: "comparison", column: "name", operator: "in", negated: false, value: [""] },
    });
});

test("parses unquoted dotted values", () => {
    assert.deepEqual(parseFilter("name=eq.a.b"), {
        where: { kind: "comparison", column: "name", operator: "eq", negated: false, value: "a.b" },
    });
});

test("parses escaped backslashes in quoted values", () => {
    assert.deepEqual(parseFilter('name=eq."a\\\\b"'), {
        where: { kind: "comparison", column: "name", operator: "eq", negated: false, value: "a\\b" },
    });
});

test("keeps a plus sign literal in a decoded value", () => {
    assert.deepEqual(parseFilter("name=eq.a+b"), {
        where: { kind: "comparison", column: "name", operator: "eq", negated: false, value: "a+b" },
    });
});

test("returns no document when no supported filter exists", () => {
    assert.equal(parseFilter("select=id,name"), undefined);
    assert.equal(parseFilter(""), undefined);
});
