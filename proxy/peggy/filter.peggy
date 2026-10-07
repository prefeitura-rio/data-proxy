// vim: set ft=js :

{
    function comparison(column, operator, value) {
        return {
            kind: "comparison",
            column: column,
            operator: operator.base,
            negated: operator.negated,
            value: value,
        };
    }

    function group(operator, items, negated) {
        var result = {
            kind: "group",
            operator: operator,
            items: items,
        };
        if (negated) {
            result.negated = true;
        }
        return result;
    }
}

Start = Expression

Expression = Group / Comparison

Group
    = negated:("not." { return true; })? operator:Logical "(" items:ExpressionList ")" {
        return group(operator, items, negated === true);
    }

ExpressionList
    = head:Expression tail:("," item:Expression { return item; })* {
        return [head].concat(tail);
    }

Comparison
    = column:Identifier "." operator:Operator "." value:Value {
        return comparison(column, operator, value);
    }

Operator
    = negated:("not." { return true; })? base:Base {
        return { base: base, negated: negated === true };
    }

Base
    = "neq" / "gte" / "lte" / "eq" / "gt" / "lt" / "in" / "is" / "like" / "ilike"

Logical
    = "and" / "or"

Identifier
    = first:[a-zA-Z_] rest:[a-zA-Z0-9_]* {
        return first + rest.join("");
    }

Value
    = List / Quoted / Scalar

List
    = "(" head:ListItem tail:("," item:ListItem { return item; })* ")" {
        return [head].concat(tail);
    }

ListItem
    = Quoted / Unquoted

Quoted
    = '"' chars:QuotedChar* '"' {
        return chars.join("");
    }

QuotedChar
    = "\\" char:. { return char; }
    / !'"' char:. { return char; }

Unquoted
    = chars:[^,)]* &EndOfItem {
        if (chars.length === 0) {
            error("expected a non-empty value");
        }
        return chars.join("");
    }

Scalar
    = chars:[^,)]* &EndOfItem {
        if (chars.length === 0) {
            error("expected a non-empty value");
        }
        return chars.join("");
    }

EndOfItem = "," / ")" / EndOfInput
EndOfInput = !.
