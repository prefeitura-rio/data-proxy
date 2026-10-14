import pytest

from sync.validation import Problem, check_config

SOURCE = '"source": {"type": "bigquery"}'


class TestCheckConfig:
    """check_config reports every problem of a draft with a JSON Pointer."""

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            pytest.param('{"schemas": {}}', [], id="empty"),
            pytest.param(
                f'{{"schemas": {{"a": {{{SOURCE}, "tables": [{{"name": "p.d.t", "strategy": "full"}}]}}}}}}',
                [],
                id="one-table",
            ),
            pytest.param(
                f'{{"schemas": {{"a": {{{SOURCE}, "tables": [{{"name": "p.d.t", "strategy": "nope"}}]}}}}}}',
                [("/schemas/a/tables/0", "Input tag 'nope'")],
                id="unknown-strategy",
            ),
            pytest.param(
                f'{{"schemas": {{"a": {{{SOURCE}, "tables": [{{"strategy": "full"}}]}}}}}}',
                [("/schemas/a/tables/0/full/name", "Field required")],
                id="missing-name",
            ),
            pytest.param(
                '{"schemas": {"a": {"source": {"type": "oracle"}, "tables": []}}}',
                [("", "unknown source: oracle")],
                id="unknown-source",
            ),
            pytest.param(
                f'{{"schemas": {{"a/b~c": {{{SOURCE}, "tables": [{{"strategy": "x"}}]}}}}}}',
                [("/schemas/a~1b~0c/tables/0", "Input tag 'x'")],
                id="escaped-pointer",
            ),
        ],
    )
    def test_reports_the_pointer_and_the_reason_of_each_problem(
        self, document: str, expected: list[tuple[str, str]]
    ) -> None:
        """Each problem names the place in the document and starts with the reason."""
        problems = check_config(document)

        assert [p.pointer for p in problems] == [pointer for pointer, _ in expected]
        assert all(
            p.message.startswith(reason)
            for p, (_, reason) in zip(problems, expected, strict=True)
        )

    def test_problems_serialize_for_the_database(self) -> None:
        """A problem is a pointer and a message."""
        problem = Problem(pointer="/schemas", message="bad")

        assert problem.model_dump() == {"pointer": "/schemas", "message": "bad"}
