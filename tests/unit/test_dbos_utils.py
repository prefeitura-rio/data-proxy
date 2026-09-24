"""Unit tests for DBOS utility behavior."""

import asyncio

import data_proxy.dbos.utils as dbos_utils


class TestTransientRetryClassification:
    """TransientRetryClassification behavior tests."""

    def test_classifies_runtime_errors_as_transient(self) -> None:
        """Classify runtime errors as transient."""
        assert dbos_utils.retry_transient(RuntimeError("temporary"))
        assert not dbos_utils.retry_transient(ValueError("invalid"))

    def test_classifies_cancellation_as_permanent(self) -> None:
        """Classify cancellation as permanent."""
        assert not dbos_utils.retry_transient(asyncio.CancelledError())
