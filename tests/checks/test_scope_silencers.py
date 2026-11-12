"""Gate 1: a comment that switches a tool off is found in the files of every language."""

import asyncio
from pathlib import Path

import pytest

from forja.checks.changes import Change, ChangeKind, Changes
from forja.checks.scope import ScopeCheck, silencers
from forja.core.adapters import AdapterRegistry
from forja.core.gates import Approved, GateInput, Rejected
from forja.languages.csharp import CSharpAdapter
from forja.languages.go import GoAdapter
from forja.languages.java import JavaAdapter
from forja.languages.python import PythonAdapter
from forja.languages.typescript import TypeScriptAdapter
from forja.orchestrator.ports import TaskInfo


def registry() -> AdapterRegistry:
    known = AdapterRegistry()
    for adapter in (
        PythonAdapter(),
        GoAdapter(),
        TypeScriptAdapter(),
        CSharpAdapter(),
        JavaAdapter(),
    ):
        known.register_language(adapter)
    return known


def judge(path: str, line: str) -> object:
    known = registry()
    patch = (f"--- a/{path}", f"+++ b/{path}", "@@ -0,0 +1 @@", f"+{line}")
    changes = Changes((Change(ChangeKind.MODIFIED, path),), 1, patch, ())
    task = TaskInfo("t1", "t1-s", "intent", (path,), 8, 400)
    data = GateInput(Path("."), "c" * 40, "t1", "t1-s", "t1-a1")
    check = ScopeCheck(changes, task, known.classify, silencers=silencers(known.for_file))
    return asyncio.run(check.evaluate(data))


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("pkg/a.go", "x := f() //nolint:errcheck"),
        ("pkg/a.go", "x := f() // nolint"),
        ("pkg/a_test.go", 't.Skip("later")'),
        ("pkg/a_test.go", 't.Skipf("later %d", 1)'),
        ("pkg/a_test.go", "t.SkipNow()"),
        ("src/a.ts", "// eslint-disable-next-line no-console"),
        ("src/a.ts", "/* eslint-disable */"),
        ("src/a.ts", "// @ts-ignore"),
        ("src/a.ts", "// @ts-nocheck"),
        ("src/a.ts", "// @ts-expect-error"),
        ("src/a.ts", "// prettier-ignore"),
        ("src/a.js", "/* istanbul ignore next */"),
        ("src/a.js", "/* c8 ignore start */"),
        ("src/main/java/A.java", '@SuppressWarnings("unchecked")'),
        ("src/main/java/A.java", '@SuppressFBWarnings("NP")'),
        ("src/main/java/A.java", "int x = 1; // NOPMD"),
        ("src/main/java/A.java", "// CHECKSTYLE:OFF"),
        ("src/main/java/A.java", "// spotless:off"),
        ("src/A.cs", "#pragma warning disable CS0168"),
        ("src/A.cs", '[SuppressMessage("Design", "CA1031")]'),
        ("src/A.cs", "[ExcludeFromCodeCoverage]"),
        ("pkg/a.go", "x := 1 // gitleaks:allow"),
        ("src/a.py", "x = 1  # noqa"),
    ],
)
def test_the_marker_of_each_language_is_found_in_its_own_files(path: str, line: str) -> None:
    result = judge(path, line)

    assert isinstance(result, Rejected)
    # a test file also breaks the rule of the role; only the marker is under test here
    found = [(f.message, f.target) for f in result.findings]
    assert ("a comment that silences a tool was added", path) in found


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("src/A.cs", "var rest = items.Skip(2).ToList();"),  # the rest of a sequence, not a test
        ("src/a.py", "rest = reader.Skip(2)"),
        ("pkg/a.go", "x := skip(2)"),
        ("src/a.ts", "const disabled = eslint;"),
        ("src/main/java/A.java", "String s = suppress(warnings);"),
    ],
)
def test_the_marker_of_one_language_is_not_looked_for_in_another(path: str, line: str) -> None:
    assert judge(path, line) == Approved()
