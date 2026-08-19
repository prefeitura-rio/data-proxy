"""Tests for the sync configuration data models."""

import pytest
from pydantic import ValidationError

from dp.models import FullTable, SchemaConfig, SyncConfig, UnitMapping


def test_unit_mapping_pairs_column_and_type() -> None:
    """A unit mapping names one row column and its unit type."""
    mapping = UnitMapping(column="id_cras", unit_type="cras")

    assert mapping.column == "id_cras"
    assert mapping.unit_type == "cras"


def test_table_accepts_a_list_of_unit_mappings() -> None:
    """A table's `rls` config is a plain list of column/unit_type pairs."""
    table = FullTable.model_validate(
        {
            "name": "p.d.t",
            "strategy": "full",
            "rls": [
                {"column": "id_cras", "unit_type": "cras"},
                {"column": "id_escola", "unit_type": "escola"},
            ],
        }
    )

    assert table.rls is not None
    assert len(table.rls) == 2
    assert table.rls[0].column == "id_cras"
    assert table.rls[1].unit_type == "escola"


def test_table_defaults_rls_to_none() -> None:
    """A table without an `rls` config is not protected by access_policy."""
    table = FullTable.model_validate({"name": "p.d.t", "strategy": "full"})

    assert table.rls is None


def test_sync_config_maps_schemas_to_their_identity_claim() -> None:
    """Each schema configures its own identity claim for access_policy checks."""
    config = SyncConfig.model_validate(
        {
            "schemas": {
                "app_x": {
                    "claim": "preferred_username",
                    "tables": [{"name": "p.d.t", "strategy": "full"}],
                }
            }
        }
    )

    assert config.schemas["app_x"].claim == "preferred_username"


def test_sync_config_defaults_schemas_to_empty() -> None:
    """A config without a `schemas` section has no schemas or tables."""
    config = SyncConfig.model_validate({})

    assert config.schemas == {}
    assert config.tables == []


def test_sync_config_tables_flattens_every_schema() -> None:
    """The `tables` property lists every table across every schema."""
    config = SyncConfig.model_validate(
        {
            "schemas": {
                "app": {"tables": [{"name": "p.app.one", "strategy": "full"}]},
                "other": {"tables": [{"name": "p.other.two", "strategy": "full"}]},
            }
        }
    )

    assert [table.name for table in config.tables] == ["p.app.one", "p.other.two"]


def test_sync_config_stamps_resolved_schema_from_nesting_key() -> None:
    """A table's schema is whatever key it is nested under, not a field of its own."""
    config = SyncConfig.model_validate(
        {"schemas": {"app": {"tables": [{"name": "p.app.one", "strategy": "full"}]}}}
    )

    assert config.tables[0].resolved_schema == "app"


def test_sync_config_rejects_rls_table_in_schema_without_claim() -> None:
    """A schema with an rls table but no claim fails validation clearly."""
    with pytest.raises(ValidationError, match="no claim but rls tables"):
        SyncConfig.model_validate(
            {
                "schemas": {
                    "app": {
                        "tables": [
                            {
                                "name": "p.app.one",
                                "strategy": "full",
                                "rls": [{"column": "id_cras", "unit_type": "cras"}],
                            }
                        ]
                    }
                }
            }
        )


def test_schema_config_claim_defaults_to_none() -> None:
    """A schema without any rls tables does not require a claim."""
    schema = SchemaConfig()

    assert schema.claim is None
    assert schema.tables == []
