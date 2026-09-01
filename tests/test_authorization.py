from typing import cast

import pytest
from psycopg import Connection

from dp.authorization import bootstrap_table
from dp.models import UnitMapping


class TestAuthorization:
    """Tests for authorization validation and bootstrap safety."""

    def test_bootstrap_rejects_an_invalid_runtime_rls_value(
        self,
        postgres: Connection[tuple[object, ...]],
    ) -> None:
        """Verify bootstrap rejects an invalid runtime rls value."""
        invalid_rls = cast("list[UnitMapping]", cast(object, "invalid"))
        with pytest.raises(AssertionError):
            bootstrap_table(
                postgres,
                "app",
                "table",
                invalid_rls,
                None,
            )
