import pytest


@pytest.mark.proxy
@pytest.mark.skip(reason="OpenResty proxy stack fixture is not wired yet")
def test_openresty_migration_contract() -> None:
    """Reserve the runtime-neutral acceptance-test entry point."""
