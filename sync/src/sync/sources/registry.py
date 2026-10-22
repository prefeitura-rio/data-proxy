"""Configured registry of external synchronization sources."""

from .bigquery.source import BigQuerySource
from .source import Sources

sources = Sources().register([BigQuerySource()])
