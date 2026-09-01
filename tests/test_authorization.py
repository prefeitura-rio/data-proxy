import pytest
from psycopg import Connection

from dp.authorization import bootstrap_table
from dp.models import UnitMapping


class TestAuthorization:
    """Tests for authorization validation and bootstrap safety."""

    def test_bootstrap_rejects_an_invalid_runtime_rls_value(
        self,
        postgres: Connection[tuple[object, ...]],
        invalid_rls: list[UnitMapping],
    ) -> None:
        """
        GIVEN: an invalid runtime RLS value.
        WHEN: bootstrap_table is called.
        THEN: it raises AssertionError.
        """
        with pytest.raises(AssertionError):
            bootstrap_table(postgres, "app", "table", invalid_rls, None)
