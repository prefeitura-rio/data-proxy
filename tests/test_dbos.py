"""Tests for DBOS utility helpers."""

import asyncio

import data_proxy.dbos.utils as dbos_utils
from data_proxy.models import SchemaConfig, SchemaWriters, SyncConfig, SyncPlan


def test_group_schema_configs_by_dsn() -> None:
    config = SyncConfig(schemas={"alpha": SchemaConfig(), "beta": SchemaConfig()})
    plans = [SyncPlan(schema_name="alpha"), SyncPlan(schema_name="beta")]
    writers = SchemaWriters(writers={"alpha": "dsn", "beta": "dsn"})

    assert dbos_utils.group_schema_configs_by_dsn(plans, config, writers) == {
        "dsn": {"alpha": config.schemas["alpha"], "beta": config.schemas["beta"]}
    }


def test_retry_transient_excludes_validation_errors() -> None:
    assert dbos_utils.retry_transient(RuntimeError("temporary"))
    assert not dbos_utils.retry_transient(ValueError("invalid"))


def test_retry_transient_excludes_system_errors() -> None:
    assert not dbos_utils.retry_transient(asyncio.CancelledError())
