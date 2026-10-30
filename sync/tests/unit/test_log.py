from logging import ERROR, INFO

import pytest

from sync.constants import LOCATION_FRAMES, PROJECT_PATH
from sync.log import ContextFormatter, summarize_exception
from tests.helpers import raised_in_project_file, record


class TestSummarizeException:
    """summarize_exception behavior tests."""

    def test_writes_the_type_and_the_message_on_one_line(self) -> None:
        """Collapse the white space of a message that spans lines."""
        assert summarize_exception(ValueError("a  b\n  c")) == "ValueError: a b c"

    def test_adds_the_project_frame_that_raised_the_error(self) -> None:
        """Point at the file and the line of the project."""
        error = raised_in_project_file(f"{PROJECT_PATH}src/sync/thing.py", 7)

        assert summarize_exception(error) == "ValueError: boom (thing.py:7)"

    def test_keeps_only_the_last_project_frames(self) -> None:
        """Name at most the last frames of the project, in order."""

        def deeper(depth: int) -> None:
            if depth == 0:
                raise ValueError("deep")

            namespace: dict[str, object] = {"deeper": deeper, "depth": depth}
            exec(  # ruff: ignore[exec-builtin]
                compile("deeper(depth - 1)", f"{PROJECT_PATH}frame{depth}.py", "exec"),
                namespace,
            )

        with pytest.raises(ValueError, match="deep") as caught:
            deeper(LOCATION_FRAMES + 2)

        summary = summarize_exception(caught.value)

        assert summary.count("->") == LOCATION_FRAMES - 1
        assert summary.endswith("frame1.py:1)")

    def test_names_no_frame_outside_the_project(self) -> None:
        """Leave out the frames of the tests and of the libraries."""
        with pytest.raises(KeyError) as caught:
            raise KeyError("x")

        assert summarize_exception(caught.value) == "KeyError: 'x'"


class TestContextFormatter:
    """ContextFormatter behavior tests."""

    def test_prefixes_the_message_with_the_workflow_the_schema_and_the_table(
        self,
    ) -> None:
        """Show the context of the domain next to the message."""
        line = record(INFO, "Dump started")
        line.operationUUID = "wf-1"
        line.schema = "app"
        line.table = "p.d.t"

        assert (
            ContextFormatter()
            .format(line)
            .endswith("INFO [wf=wf-1 schema=app table=p.d.t] Dump started")
        )

    def test_leaves_out_the_context_that_it_does_not_have(self) -> None:
        """Write no brackets for a line with no context."""
        line = record(INFO, "Started")
        line.schema = "-"
        line.table = "-"

        assert ContextFormatter().format(line).endswith("INFO Started")

    def test_summarizes_the_exception_of_a_line_below_error(self) -> None:
        """Keep a warning with an exception on one line."""
        line = record(INFO, "Retry", error=ValueError("slow"))

        assert ContextFormatter().format(line).endswith("INFO Retry: ValueError: slow")

    def test_writes_the_whole_traceback_of_an_error(self) -> None:
        """Give an error line the stack that explains it."""
        with pytest.raises(ValueError, match="broken") as caught:
            raise ValueError("broken")

        line = record(ERROR, "Failed", error=caught.value)

        text = ContextFormatter().format(line)

        assert "ERROR Failed\nTraceback" in text
        assert text.endswith("ValueError: broken")
