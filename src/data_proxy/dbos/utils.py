"""DBOS utility functions."""

from duckdb import TransactionException

from ..settings import Settings


def endpoint_list(endpoint: str) -> list[str]:
    """Return one configured endpoint or an empty list."""
    return [endpoint] if endpoint else []


def otlp_enabled(application_settings: Settings) -> bool:
    """Return whether any OTLP endpoint is configured."""
    return any(
        (
            application_settings.OTLP_LOGS_ENDPOINT,
            application_settings.OTLP_TRACES_ENDPOINT,
            application_settings.OTLP_METRICS_ENDPOINT,
        )
    )


def retry_catalog_locked(error: BaseException) -> bool:
    """Retry only while another process holds the DuckLake catalog write lock."""
    match error:
        case TransactionException() if "database is locked" in str(error):
            return True
        case _:
            return False


def retry_transient(error: BaseException) -> bool:
    """Retry backend failures but not input validation errors."""
    match error:
        case ValueError():
            return False
        case Exception():
            return True
        case _:
            return False
