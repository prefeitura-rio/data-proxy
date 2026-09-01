from typing import cast

import pytest
from psycopg import Connection
from pydantic import ValidationError

from dp.authorization import bootstrap_table
from dp.models import FullTable, UnitMapping


class TestAuthorization:
    """Tests for authorization validation and bootstrap safety."""

    def test_authorization_rejects_invalid_rls_shape(
        self,
    ) -> None:
        """Verify authorization rejects invalid rls shape."""
        with pytest.raises(ValidationError):
            FullTable.model_validate({"name": "p.d.table", "rls": "invalid"})

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
