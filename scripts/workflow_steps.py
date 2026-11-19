"""Print the fingerprint of the steps that the DBOS workflows run.

DBOS replays the saved steps of a workflow in order, so a release that adds, removes or reorders them needs a new application version.
The fingerprint covers each workflow name, its parameters and its awaited calls in source order. It skips log calls, comments and docstrings.
"""

import ast
import hashlib
import json
import sys
from pathlib import Path


def call_name(call: ast.Call) -> str:
    """Return the dotted name of the callee, or its last part for another expression."""
    func = call.func

    if isinstance(func, ast.Name):
        return func.id

    if isinstance(func, ast.Attribute):
        return func.attr

    return ast.dump(func)


def is_log_call(call: ast.Call) -> bool:
    """Return true for a call on the `logs` module, which changes no step."""
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "logs"
    )


def is_workflow(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    """Return true when the function has the decorator `DBOS.workflow`."""
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator

        if isinstance(target, ast.Attribute) and target.attr == "workflow":
            return True

    return False


class Steps(ast.NodeVisitor):
    """Collect the awaited calls of one function, in source order."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def visit_Await(self, node: ast.Await) -> None:
        """Record the call that the await waits for, then read its arguments."""
        if isinstance(node.value, ast.Call) and not is_log_call(node.value):
            name = call_name(node.value)
            self.names.append(name)

            if name.startswith(("enqueue_workflow", "start_workflow")):
                self.names.extend(
                    argument.id
                    for argument in node.value.args
                    if isinstance(argument, ast.Name)
                )

        self.generic_visit(node)


def workflow_steps(source: str) -> list[dict[str, object]]:
    """Return the name, parameters and steps of every workflow in the source."""
    workflows: list[dict[str, object]] = []

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue

        if not is_workflow(node):
            continue

        steps = Steps()

        for statement in node.body:
            steps.visit(statement)

        workflows.append(
            {
                "name": node.name,
                "parameters": [argument.arg for argument in node.args.args],
                "steps": steps.names,
            }
        )

    return sorted(workflows, key=lambda item: str(item["name"]))


def fingerprint(source: str) -> str:
    """Return the SHA-256 of the workflow steps in the source."""
    document = json.dumps(workflow_steps(source), sort_keys=True)
    return hashlib.sha256(document.encode()).hexdigest()


if __name__ == "__main__":
    path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path("sync/src/sync/dbos/workflows.py")
    )
    print(fingerprint(path.read_text()))
