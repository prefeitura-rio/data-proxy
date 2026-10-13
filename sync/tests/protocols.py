"""Protocols used by test fixtures."""

from collections.abc import Sequence
from typing import Protocol


class BigQueryScalarParameter(Protocol):
    """Query parameter shape used by the BigQuery mock."""

    value: str


class BigQueryQueryConfig(Protocol):
    """Query configuration shape used by the BigQuery mock."""

    query_parameters: Sequence[BigQueryScalarParameter]
