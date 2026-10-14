"""PostgreSQL fallback metadata for BigQuery."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BigQueryFallback:
    """Describe the BigQuery PostgreSQL fallback helper."""

    suffix: str = "bq_fn"
    template: str = "postgres/views/fallbacks/bigquery"
