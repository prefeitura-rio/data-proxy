from ..models import SchemaConfig, SchemaWriters, SyncConfig, SyncPlan
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


def group_schema_configs_by_dsn(
    plans: list[SyncPlan],
    config: SyncConfig,
    schema_writers: SchemaWriters,
) -> dict[str, dict[str, SchemaConfig]]:
    """Group planned schema configurations by writer DSN."""
    by_dsn: dict[str, dict[str, SchemaConfig]] = {}

    for plan in plans:
        schema_name = plan.schema_name
        dsn = schema_writers.dsn(schema_name)
        by_dsn.setdefault(dsn, {})[schema_name] = config.schemas[schema_name]

    return by_dsn
