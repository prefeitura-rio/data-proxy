"""Shared fixtures for the data-proxy test suite.

Service fixtures live under ``tests/fixtures`` and are registered as plugins.
"""

pytest_plugins = [
    "tests.fixtures.api",
    "tests.fixtures.db",
    "tests.fixtures.unit",
    "tests.integration.steps",
]
