"""Reject SQL template rendering and database execution outside the adapter."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1] / "src" / "dp"
ADAPTER = ROOT / "templates.py"


def main() -> int:
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path == ADAPTER or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.endswith("templates")
                and any(alias.name == "render_template" for alias in node.names)
            ):
                violations.append(f"{path}: imports render_template")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and not _is_redis_pipeline(node.func.value)
            ):
                violations.append(f"{path}:{node.lineno}: calls execute")
    if violations:
        print("\n".join(violations))
        return 1
    return 0


def _is_redis_pipeline(value: ast.expr) -> bool:
    return isinstance(value, ast.Name) and value.id == "pipe"


if __name__ == "__main__":
    raise SystemExit(main())
