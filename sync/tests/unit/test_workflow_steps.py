import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from sync.settings import Settings

ROOT = Path(__file__).resolve().parents[3]


def load_script() -> ModuleType:
    """Load scripts/workflow_steps.py, which is not a package."""
    spec = importlib.util.spec_from_file_location(
        "workflow_steps", ROOT / "scripts" / "workflow_steps.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


steps = load_script()

BASE = '''
@DBOS.workflow()
async def flow(run_id):
    """Run one flow."""
    await logs.info("start", run_id=run_id)
    await first()
    await second(run_id)
'''


class TestFingerprint:
    """The fingerprint changes when the steps of a workflow change, and only then."""

    @pytest.mark.parametrize(
        ("source", "changes"),
        [
            pytest.param(BASE, False, id="same-source"),
            pytest.param(BASE + "\n# a comment\n", False, id="comment"),
            pytest.param(
                BASE.replace("Run one flow.", "Run the flow."), False, id="docstring"
            ),
            pytest.param(BASE.replace('"start"', '"begin"'), False, id="log-message"),
            pytest.param(
                BASE + "\nasync def helper():\n    await other()\n",
                False,
                id="function-without-the-decorator",
            ),
            pytest.param(
                BASE.replace(
                    "await second(run_id)", "await second(run_id)\n    await third()"
                ),
                True,
                id="step-added",
            ),
            pytest.param(
                BASE.replace("    await first()\n", ""), True, id="step-removed"
            ),
            pytest.param(
                BASE.replace(
                    "await first()\n    await second(run_id)",
                    "await second(run_id)\n    await first()",
                ),
                True,
                id="steps-reordered",
            ),
            pytest.param(
                BASE.replace("def flow(run_id)", "def flow(run_id, extra)"),
                True,
                id="parameter-added",
            ),
            pytest.param(
                BASE + "\n@DBOS.workflow()\nasync def other():\n    await one()\n",
                True,
                id="workflow-added",
            ),
        ],
    )
    def test_follows_the_steps_of_the_workflows(
        self, source: str, changes: bool
    ) -> None:
        """A comment, a docstring or a log line leaves it as is. A step or a parameter changes it."""
        assert (steps.fingerprint(source) != steps.fingerprint(BASE)) is changes

    def test_follows_the_workflow_that_an_enqueue_starts(self) -> None:
        """Another workflow in the queue is another step, so the replay differs."""
        first = "@DBOS.workflow()\nasync def f(x):\n    await DBOS.enqueue_workflow_async(Q, a, x)\n"
        second = first.replace(", a, x", ", b, x")

        assert steps.fingerprint(first) != steps.fingerprint(second)


class TestVersionFiles:
    """The three places of the application version and the fingerprint agree."""

    def test_keeps_the_chart_and_the_setting_on_one_version(self) -> None:
        """The chart value and the default of the setting must match, or a pod runs another version."""
        values = (ROOT / "helm" / "values.yaml").read_text()
        chart_version = next(
            line.split(":", maxsplit=1)[1].strip()
            for line in values.splitlines()
            if line.strip().startswith("applicationVersion:")
        )

        assert chart_version == Settings().DBOS_APPLICATION_VERSION

    def test_keeps_the_stored_fingerprint_of_the_workflows(self) -> None:
        """A change of the steps needs the release hook, which writes the new fingerprint."""
        source = (ROOT / "sync" / "src" / "sync" / "dbos" / "workflows.py").read_text()
        stored = (ROOT / "sync" / "workflow-steps.sha256").read_text().strip()

        assert steps.fingerprint(source) == stored
