from ..models import SchemaConfig, SchemaWriters, SyncConfig, SyncPlan


def retry_transient(error: BaseException) -> bool:
    """Retry backend failures but not input validation errors."""
    return not isinstance(error, ValueError)


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
