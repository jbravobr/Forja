"""The checks sit below the orchestrator: what they need from a run comes from ``core``."""

import ast
from pathlib import Path

import forja.checks

CHECKS = Path(forja.checks.__file__).parent


def _imported(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_no_check_imports_the_orchestrator() -> None:
    offenders = {
        path.relative_to(CHECKS).as_posix(): sorted(
            module
            for module in _imported(path.read_text(encoding="utf-8"))
            if module.startswith("forja.orchestrator")
        )
        for path in sorted(CHECKS.rglob("*.py"))
    }

    assert {name: found for name, found in offenders.items() if found} == {}
