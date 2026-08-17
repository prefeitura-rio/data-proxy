"""Tests for the sync configuration data models."""

import pytest

from dp.models import RlsConfig


def test_rls_accepts_column_mode() -> None:
    """Column-based RLS remains the default single-column mode."""
    config = RlsConfig(column="unit_id")

    assert config.column == "unit_id"
    assert config.policy is None


def test_rls_accepts_policy_mode() -> None:
    """Policy-based RLS names the custom policy template."""
    config = RlsConfig(policy="participant_access")

    assert config.column is None
    assert config.policy == "participant_access"


@pytest.mark.parametrize("invalid", [{}, {"column": "x", "policy": "p"}])
def test_rls_rejects_missing_or_conflicting_modes(invalid: dict[str, str]) -> None:
    """RLS requires exactly one of column or policy."""
    with pytest.raises(ValueError, match="exactly one"):
        RlsConfig(**invalid)
