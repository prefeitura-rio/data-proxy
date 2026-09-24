"""Unit tests for Kubernetes template and readiness logic."""

from types import SimpleNamespace
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from data_proxy.kubernetes import Deployment, deployment_ready
from tests.helpers import deployment_value


class TestDeploymentReadiness:
    """Deployment readiness behavior tests."""

    @given(missing=st.integers(1, 7))
    @pytest.mark.asyncio
    async def test_rejects_missing_deployment_fields(self, missing: int) -> None:
        """Reject a deployment with any required field missing."""
        deployment = cast(
            Deployment,
            cast(
                object,
                SimpleNamespace(
                    status=None
                    if missing & 1
                    else SimpleNamespace(
                        updated_replicas=1, available_replicas=1, observed_generation=1
                    ),
                    spec=None if missing & 2 else SimpleNamespace(replicas=1),
                    metadata=None if missing & 4 else SimpleNamespace(generation=1),
                ),
            ),
        )
        with pytest.raises(RuntimeError, match="not ready"):
            await deployment_ready(lambda: deployment_value(deployment))

    @given(replicas=st.integers(0, 10))
    @pytest.mark.asyncio
    async def test_rejects_stale_updated_replicas(self, replicas: int) -> None:
        """Reject a deployment with stale updated replicas."""
        deployment = cast(
            Deployment,
            cast(
                object,
                SimpleNamespace(
                    status=SimpleNamespace(
                        updated_replicas=replicas + 1,
                        available_replicas=replicas,
                        observed_generation=1,
                    ),
                    spec=SimpleNamespace(replicas=replicas),
                    metadata=SimpleNamespace(generation=1),
                ),
            ),
        )
        with pytest.raises(RuntimeError, match="not ready"):
            await deployment_ready(lambda: deployment_value(deployment))

    @given(generation=st.integers(0, 10))
    @pytest.mark.asyncio
    async def test_rejects_stale_observed_generation(self, generation: int) -> None:
        """Reject a deployment with a stale observed generation."""
        deployment = cast(
            Deployment,
            cast(
                object,
                SimpleNamespace(
                    status=SimpleNamespace(
                        updated_replicas=1,
                        available_replicas=1,
                        observed_generation=generation,
                    ),
                    spec=SimpleNamespace(replicas=1),
                    metadata=SimpleNamespace(generation=generation + 1),
                ),
            ),
        )
        with pytest.raises(RuntimeError, match="not ready"):
            await deployment_ready(lambda: deployment_value(deployment))

    @given(replicas=st.integers(0, 10), generation=st.integers(0, 10))
    @pytest.mark.asyncio
    async def test_accepts_matching_status(
        self, replicas: int, generation: int
    ) -> None:
        """Accept a deployment whose status matches its specification."""
        deployment = cast(
            Deployment,
            cast(
                object,
                SimpleNamespace(
                    status=SimpleNamespace(
                        updated_replicas=replicas,
                        available_replicas=replicas,
                        observed_generation=generation,
                    ),
                    spec=SimpleNamespace(replicas=replicas),
                    metadata=SimpleNamespace(generation=generation),
                ),
            ),
        )
        await deployment_ready(lambda: deployment_value(deployment))

    @given(replicas=st.integers(0, 10), available=st.integers(0, 10))
    @pytest.mark.asyncio
    async def test_rejects_unavailable_replicas(
        self, replicas: int, available: int
    ) -> None:
        """Reject a deployment whose available replicas do not match."""
        if available == replicas:
            return
        deployment = cast(
            Deployment,
            cast(
                object,
                SimpleNamespace(
                    status=SimpleNamespace(
                        updated_replicas=replicas,
                        available_replicas=available,
                        observed_generation=1,
                    ),
                    spec=SimpleNamespace(replicas=replicas),
                    metadata=SimpleNamespace(generation=1),
                ),
            ),
        )
        with pytest.raises(RuntimeError, match="not ready"):
            await deployment_ready(lambda: deployment_value(deployment))
