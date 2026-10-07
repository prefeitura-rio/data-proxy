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

test("parses order defaults and null placement", () => {
    assert.deepEqual(parseFilter("id=eq.1&order=name.nullsfirst"), {
        where: {
            kind: "comparison",
            column: "id",
            operator: "eq",
            negated: false,
            value: "1",
        },
        order: [{ column: "name", direction: "asc", nulls: "first" }],
    });
});

test("parses order and pagination modifiers", () => {
    assert.deepEqual(
        parseFilter("id=gte.10&order=id.desc.nullslast,name.asc.nullsfirst&limit=25&offset=5"),
        {
            where: {
                kind: "comparison",
                column: "id",
                operator: "gte",
                negated: false,
                value: "10",
            },
            order: [
                { column: "id", direction: "desc", nulls: "last" },
                { column: "name", direction: "asc", nulls: "first" },
            ],
            limit: 25,
            offset: 5,
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

test("does not emit modifiers while an unsupported predicate remains", () => {
    assert.deepEqual(parseFilter("id=eq.42&name=fts.search&order=id.desc&limit=1"), {
        where: {
            kind: "comparison",
            column: "id",
            operator: "eq",
            negated: false,
            value: "42",
        },
    });
});

test("returns no document for malformed supported syntax", () => {
    assert.equal(parseFilter("id=eq"), undefined);
    assert.equal(parseFilter("id=in.(one,,two)"), undefined);
    assert.equal(parseFilter("id=in.one"), undefined);
    assert.equal(parseFilter("id=eq.(one)"), undefined);
    assert.equal(parseFilter("id=is.maybe"), undefined);
    assert.equal(parseFilter("id=eq.%ZZ"), undefined);
    assert.equal(parseFilter("order="), undefined);
    assert.equal(parseFilter("order=id.side"), undefined);
    assert.equal(parseFilter("order=id.desc.foo"), undefined);
    assert.equal(parseFilter("limit=-1"), undefined);
    assert.equal(parseFilter("and=id.eq.1"), undefined);
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

test("returns no document when no supported filter exists", () => {
    assert.equal(parseFilter("select=id,name"), undefined);
    assert.equal(parseFilter(""), undefined);
});
