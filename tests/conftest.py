"""Shared fixtures for the data-proxy test suite.

Service fixtures live under ``tests/fixtures`` and are registered as plugins.
"""

import os

os.environ.setdefault("SCHEMA_WRITERS", '{"writers":{"test":"postgresql://test"}}')

pytest_plugins = [
    "tests.fixtures.api",
    "tests.fixtures.db",
]
