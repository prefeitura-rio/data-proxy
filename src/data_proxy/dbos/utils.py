"""DBOS utility functions."""

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


def retry_transient(error: BaseException) -> bool:
    """Retry backend failures but not input validation errors."""
    return isinstance(error, Exception) and not isinstance(error, ValueError)
