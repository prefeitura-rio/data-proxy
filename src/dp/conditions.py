"""SQL predicate builders for partition, scan, and authorization filters."""

from typing import assert_never

from psycopg.sql import SQL, Composable, Identifier, Literal

from .models import (
    PhysicalPartition,
    RangeSelection,
    RemainderSelection,
    TimeRangeSelection,
    UnitMapping,
)


def selection_condition(
    selection: RangeSelection | TimeRangeSelection | RemainderSelection,
    *,
    scan: bool = False,
) -> Composable:
    """Return the predicate for one selection.

    Use identifiers for table predicates and Parquet expressions for scan predicates.
    """
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

    column_value = SQL("r[{}]").format(Literal(column)) if scan else Identifier(column)

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


def scan_condition(partition: PhysicalPartition) -> Composable:
    """Return the SQL predicate that selects one partition from a batch scan."""
    return selection_condition(partition.selection, scan=True)


def schema_scope_condition(schema: str) -> Composable:
    """Return the schema-claim condition."""
    return SQL("{} = ANY(string_to_array(current_setting({}, true), ','))").format(
        Literal(schema), Literal("app.claim_schemas")
    )


def unit_access_condition(mappings: list[UnitMapping]) -> Composable:
    """Return the unit-access condition."""
    return SQL(" OR ").join(
        SQL("(p.unit_type = {} AND p.unit_id = {}::text)").format(
            Literal(mapping.unit_type), Identifier(mapping.column)
        )
        for mapping in mappings
    )
