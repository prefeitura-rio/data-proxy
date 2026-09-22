"""Unit tests for authorization validation."""

import pytest
from psycopg import AsyncConnection

from data_proxy.authorization import apply_table_authorization
from data_proxy.models import UnitMapping


class TestAuthorizationValidation:
    """Authorization validation behavior tests."""

    @pytest.mark.asyncio
    async def test_rejects_invalid_runtime_rls_value(
        self,
        invalid_rls: list[UnitMapping],
        async_connection_double: AsyncConnection,
    ) -> None:
        """Reject an invalid runtime RLS value."""
        with pytest.raises(AssertionError):
            await apply_table_authorization(
                async_connection_double,
                "app",
                "table",
                invalid_rls,
                None,
            )

    @pytest.mark.asyncio
    async def test_rejects_protected_table_without_identity_claim(
        self, async_connection_double: AsyncConnection
    ) -> None:
        """Reject a protected table without an identity claim."""
        with pytest.raises(RuntimeError, match="identity claim"):
            await apply_table_authorization(
                async_connection_double,
                schema="app",
                table_name="table",
                rls=[UnitMapping(column="unit_id", unit_type="unit")],
                claim=None,
            )
