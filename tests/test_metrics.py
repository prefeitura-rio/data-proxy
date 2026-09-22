"""Tests for workflow metric observation."""

from unittest.mock import patch

import pytest

from data_proxy.metrics import RunStatus, configure_metrics, observe_sync
from data_proxy.settings import settings


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["success", "no_changes"])
async def test_observe_sync_records_terminal_status(status: RunStatus) -> None:
    recorded: list[RunStatus] = []

    async def record(value: RunStatus) -> None:
        recorded.append(value)

    @observe_sync(record)
    async def workflow() -> RunStatus:
        return status

    assert await workflow() is None
    assert recorded == [status]


@pytest.mark.asyncio
async def test_observe_sync_records_failure_and_reraises() -> None:
    recorded: list[RunStatus] = []

    async def record(value: RunStatus) -> None:
        recorded.append(value)

    @observe_sync(record)
    async def workflow() -> RunStatus:
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError, match="failed"):
        await workflow()

    assert recorded == ["failure"]


def test_configure_metrics_skips_empty_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "OTLP_METRICS_ENDPOINT", "")
    with patch("data_proxy.metrics.OTLPMetricExporter") as exporter:
        configure_metrics()

    exporter.assert_not_called()


def test_configure_metrics_sets_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OTLP_METRICS_ENDPOINT", "http://otel/v1/metrics")
    with (
        patch("data_proxy.metrics.OTLPMetricExporter") as exporter,
        patch("data_proxy.metrics.PeriodicExportingMetricReader") as reader,
        patch("data_proxy.metrics.MeterProvider") as provider,
        patch("data_proxy.metrics.otel_metrics.set_meter_provider") as set_provider,
    ):
        configure_metrics()

    exporter.assert_called_once_with(endpoint="http://otel/v1/metrics")
    reader.assert_called_once_with(exporter.return_value)
    provider.assert_called_once_with(metric_readers=[reader.return_value])
    set_provider.assert_called_once_with(provider.return_value)
