"""Unit tests for SQL template rendering."""

from pathlib import Path

import pytest
from jinja2 import UndefinedError
from psycopg.sql import Identifier

from data_proxy.templates import render_template


class TestTemplateRendering:
    """SQL template rendering behavior tests."""

    def test_converts_composable_values_to_sql(self, tmp_path: Path) -> None:
        """Convert a composable identifier before rendering SQL."""
        (tmp_path / "query.sql").write_text("SELECT {{ table }};\n")
        rendered = render_template(
            "query", {"table": Identifier("people")}, root=tmp_path
        )
        assert rendered == 'SELECT "people";\n'

    def test_preserves_template_trailing_newline(self, tmp_path: Path) -> None:
        """Preserve the template trailing newline."""
        (tmp_path / "query.sql").write_text("SELECT 1;\n")
        assert render_template("query", {}, root=tmp_path) == "SELECT 1;\n"

    def test_rejects_missing_template_value(self, tmp_path: Path) -> None:
        """Reject a template value that is missing from the mapping."""
        (tmp_path / "query.sql").write_text("SELECT {{ missing }};")
        with pytest.raises(UndefinedError):
            render_template("query", {}, root=tmp_path)
