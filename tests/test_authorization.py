from typing import cast

import pytest
from pydantic import ValidationError

from dp.authorization import bootstrap_table
from dp.models import FullTable, UnitMapping
from tests.helpers import FakePgConn


def test_authorization_rejects_invalid_rls_shape() -> None:
    with pytest.raises(ValidationError):
        FullTable.model_validate({"name": "p.d.table", "rls": "invalid"})


def test_bootstrap_rejects_an_invalid_runtime_rls_value() -> None:
    invalid_rls = cast("list[UnitMapping]", cast(object, "invalid"))
    with pytest.raises(AssertionError):
        bootstrap_table(
            (FakePgConn()),
            "app",
            "table",
            invalid_rls,
            None,
        )
