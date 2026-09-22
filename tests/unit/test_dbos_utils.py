"""Unit tests for DBOS utility behavior."""

import asyncio

import data_proxy.dbos.utils as dbos_utils
from data_proxy.models import SchemaConfig, SchemaWriters, SyncConfig, SyncPlan


class TestSchemaConfigurationGrouping:
    """SchemaConfigurationGrouping behavior tests."""

    def test_groups_schemas_by_shared_dsn(self) -> None:
        """Group schemas that use the same DSN."""
        config = SyncConfig(schemas={"alpha": SchemaConfig(), "beta": SchemaConfig()})
        plans = [SyncPlan(schema_name="alpha"), SyncPlan(schema_name="beta")]
        writers = SchemaWriters(writers={"alpha": "dsn", "beta": "dsn"})
        assert dbos_utils.group_schema_configs_by_dsn(plans, config, writers) == {
            "dsn": {"alpha": config.schemas["alpha"], "beta": config.schemas["beta"]}
        }


class TestTransientRetryClassification:
    """TransientRetryClassification behavior tests."""

    def test_classifies_runtime_errors_as_transient(self) -> None:
        """Classify runtime errors as transient."""
        assert dbos_utils.retry_transient(RuntimeError("temporary"))
        assert not dbos_utils.retry_transient(ValueError("invalid"))

    def test_classifies_cancellation_as_permanent(self) -> None:
        """Classify cancellation as permanent."""
        assert not dbos_utils.retry_transient(asyncio.CancelledError())
