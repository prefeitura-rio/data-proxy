"""Shared fixtures for the data-proxy test suite.

Service fixtures live under ``tests/fixtures`` and are registered as plugins.
"""

from logging import getLogger

import pytest

logger = getLogger("data_proxy")


def pytest_bdd_before_scenario(
    request: pytest.FixtureRequest, feature: object, scenario: object
) -> None:
    """Log before a BDD scenario starts."""
    logger.info(
        "BDD scenario started: %s > %s",
        getattr(feature, "name", str(feature)),
        getattr(scenario, "name", str(scenario)),
    )


def pytest_bdd_after_scenario(
    request: pytest.FixtureRequest, feature: object, scenario: object
) -> None:
    """Log after a BDD scenario completes."""
    logger.info(
        "BDD scenario completed: %s > %s",
        getattr(feature, "name", str(feature)),
        getattr(scenario, "name", str(scenario)),
    )


pytest_plugins = [
    "tests.fixtures.api",
    "tests.fixtures.db",
    "tests.integration.steps.state",
    "tests.integration.steps.schema",
    "tests.integration.steps.authorization",
    "tests.integration.steps.freshness",
    "tests.integration.steps.publication",
    "tests.integration.steps.loading",
    "tests.integration.steps.fallback",
    "tests.integration.steps.replication",
]
