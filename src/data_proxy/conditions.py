"""SQL predicate builders for partition, scan, and authorization filters."""

from typing import assert_never

from psycopg.sql import SQL, Composable, Identifier, Literal

from .models import (
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
)


def selection_condition(
    selection: RangeSelection | TimeRangeSelection | RemainderSelection,
) -> Composable:
    """Return the predicate for one selection."""
    match selection:
        case (
            RangeSelection(column=column, lower=lower, upper=upper)
            | TimeRangeSelection(column=column, lower=lower, upper=upper)
        ):
            predicate_kind = "range"
        case RemainderSelection(column=column, start=lower, end=upper):
            predicate_kind = "remainder"
        case _:  # pragma: no cover
            assert_never(selection)

    column_value = Identifier(column)

    if predicate_kind == "remainder":
        return SQL("({} IS NULL OR {} < {} OR {} >= {})").format(
            column_value, column_value, Literal(lower), column_value, Literal(upper)
        )

    return SQL("({} >= {} AND {} < {})").format(
        column_value, Literal(lower), column_value, Literal(upper)
    )


def partition_condition(partition: PhysicalPartition) -> Composable:
    """Return the SQL predicate that matches one partition."""
    return selection_condition(partition.selection)


def schema_scope_condition(schema: str) -> Composable:
    """Return the schema-claim condition."""
    return SQL("{} = ANY(string_to_array(current_setting({}, true), ','))").format(
        Literal(schema), Literal("app.claim_schemas")
    )
