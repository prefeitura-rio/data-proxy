"""Black-box acceptance tests for the OpenResty migration.

These tests are added in the next migration phase, after the proxy stack
fixture can start a real OpenResty image.
"""

import pytest


@pytest.mark.proxy
@pytest.mark.skip(reason="OpenResty proxy stack fixture is not wired yet")
def test_openresty_migration_contract() -> None:
    """Reserve the runtime-neutral acceptance-test entry point."""
