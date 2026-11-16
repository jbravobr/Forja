"""The tests of each line, the filter and the stop, with the .NET SDK and dotnet-coverage for real.

The project is the one of ``test_mutation``, with a second test file that also runs ``Add``. The
tests are skipped without the SDK, or without dotnet-coverage on the PATH.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from forja.checks.mutation import failed_test
from forja.core.adapters import (
    CommandPlan,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
)
from forja.core.gates.models import Approved, Rejected
from forja.core.reports import ReportFormat
from forja.core.reports.formats import TEST_LINES_DIRECTORY
from forja.core.reports.readers import read_test_lines
from forja.languages.csharp import CSharpAdapter
from forja.languages.csharp.adapter import find_dotnet_coverage
from tests.core.idempotency.conftest import Repo
from tests.languages.csharp.test_mutation import (
    BASE,
    SOLUTION,
    TEST_THAT_CHECKS,
    TEST_THAT_ONLY_CALLS,
    judge,
    write,
)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("dotnet") is None, reason="the .NET SDK is not installed"),
]

COVERAGE = find_dotnet_coverage(os.environ)
needs_coverage = pytest.mark.skipif(COVERAGE is None, reason="dotnet-coverage is not installed")

# Line 7 is the body of Add, run by both test files; line 12 is the body of Unused, run by none.
CALC = (
    "namespace Calc;\n\npublic static class Calculator\n{\n"
    "    public static int Add(int a, int b)\n    {\n        return a + b;\n    }\n\n"
    "    public static int Unused(int a)\n    {\n        return a * 2;\n    }\n}\n"
)
FIRST = (
    "using Calc;\n\nnamespace Calc.Tests;\n\npublic class CalculatorTests\n{\n"
    "    [Fact]\n    public void Adds()\n    {\n        Assert.Equal(5, Calculator.Add(2, 3));\n"
    "    }\n}\n"
)
SECOND = (
    "using Calc;\n\nnamespace Calc.Tests;\n\npublic class MoreTests\n{\n"
    "    [Fact]\n    public void AddsAgain()\n    {\n"
    "        Assert.Equal(4, Calculator.Add(1, 3));\n    }\n}\n"
)
WRONG = (
    "using Calc;\n\nnamespace Calc.Tests;\n\npublic class BrokenTests\n{\n"
    "    [Fact]\n    public void Wrong()\n    {\n        Assert.Equal(3, Calculator.Add(1, 1));\n"
    "    }\n}\n"
)
ADDS = "Calc.Tests.CalculatorTests.Adds"
ADDS_AGAIN = "Calc.Tests.MoreTests.AddsAgain"
WRONG_ID = "Calc.Tests.BrokenTests.Wrong"


def make_project(root: Path, extra: dict[str, str]) -> None:
    (root / "Calc.sln").write_text(SOLUTION, encoding="utf-8")
    files = {
        **BASE,
        "src/Calc/Calc.cs": CALC,
        "tests/Calc.Tests/CalculatorTests.cs": FIRST,
        "tests/Calc.Tests/MoreTests.cs": SECOND,
        **extra,
    }
    for name, text in files.items():
        write(root, name, text)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    make_project(root, {})
    return root


def adapter() -> CSharpAdapter:
    assert COVERAGE is not None
    return CSharpAdapter(dotnet_coverage=COVERAGE)


def execute(plan: CommandPlan, log: Path) -> int:
    """Run a plan with its variables, the way the core runs it, and keep the output."""
    env = {**os.environ, **plan.env_set}
    with log.open("wb") as out:
        done = subprocess.run(
            list(plan.argv),
            cwd=plan.cwd,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return done.returncode


def read_outcomes(code: int, log: Path, root: Path) -> dict[str, str]:
    parsed = adapter().parse(Operation.TEST, ToolOutput(code, log, root))
    assert isinstance(parsed, Parsed), parsed
    return {d.target.id: str(d.value) for d in parsed.drafts if d.measurement == "test.outcome"}


def measure(root: Path, tmp_path: Path) -> dict[str, dict[int, frozenset[str]]]:
    plan = adapter().plan(Operation.TEST_LINES, OperationRequest(root=root))
    assert isinstance(plan, CommandPlan)
    assert execute(plan, tmp_path / "lines.log") == 0
    directory = root / TEST_LINES_DIRECTORY
    texts = {p.name: p.read_text(encoding="utf-8") for p in directory.glob("*.xml")}
    index = (directory / "index.json").read_text(encoding="utf-8")
    found = read_test_lines(ReportFormat.COBERTURA_XML, texts, root, (), index)
    return {path: dict(lines) for path, lines in found.items()}


@needs_coverage
def test_a_line_names_each_test_that_runs_it_and_an_unrun_line_names_none(
    project: Path, tmp_path: Path
) -> None:
    lines = measure(project, tmp_path)["src/Calc/Calc.cs"]

    assert lines[7] == frozenset({ADDS, ADDS_AGAIN})
    assert 12 not in lines


@needs_coverage
def test_the_ids_of_a_line_run_exactly_those_tests(project: Path, tmp_path: Path) -> None:
    ids = tuple(sorted(measure(project, tmp_path)["src/Calc/Calc.cs"][7]))
    plan = adapter().plan(Operation.TEST, OperationRequest(root=project, tests=ids))
    assert isinstance(plan, CommandPlan)
    log = tmp_path / "filtered.log"

    code = execute(plan, log)

    assert code == 0
    assert set(read_outcomes(code, log, project)) == set(ids)


@needs_coverage
def test_the_named_tests_stop_at_the_first_that_fails_and_that_one_is_named(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    make_project(root, {"tests/Calc.Tests/BrokenTests.cs": WRONG})
    asked = (WRONG_ID, ADDS, ADDS_AGAIN)
    plan = adapter().plan(Operation.TEST, OperationRequest(root=root, tests=asked, rerun=True))
    assert isinstance(plan, CommandPlan)
    log = tmp_path / "stop.log"

    code = execute(plan, log)

    assert code != 0
    assert read_outcomes(code, log, root) == {WRONG_ID: "failed"}
    assert failed_test(adapter(), ToolOutput(code, log, root), asked) == WRONG_ID


@needs_coverage
def test_a_whole_run_with_rerun_stops_at_the_failure(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    make_project(root, {"tests/Calc.Tests/BrokenTests.cs": WRONG})
    plan = adapter().plan(Operation.TEST, OperationRequest(root=root, rerun=True))
    assert isinstance(plan, CommandPlan)
    log = tmp_path / "whole.log"

    code = execute(plan, log)

    assert code != 0
    assert read_outcomes(code, log, root)[WRONG_ID] == "failed"


def test_without_the_tool_the_tests_of_each_line_are_not_planned(project: Path) -> None:
    assert isinstance(
        CSharpAdapter().plan(Operation.TEST_LINES, OperationRequest(root=project)), NotSupported
    )


@needs_coverage
@pytest.mark.parametrize("with_lines", [False, True])
def test_a_new_method_the_tests_check_is_approved_with_or_without_the_tests_of_each_line(
    repo: Repo, tmp_path: Path, with_lines: bool
) -> None:
    history = tmp_path / "history.json"
    tool = adapter() if with_lines else CSharpAdapter()

    result = judge(repo, tmp_path, TEST_THAT_CHECKS, tool, history, line_tests=True)

    assert isinstance(result, Approved)
    if with_lines:  # the test that killed each mutant is kept
        assert "Calc.Tests.CalculatorTests.Subtracts" in history.read_text(encoding="utf-8")


@needs_coverage
@pytest.mark.parametrize("with_lines", [False, True])
def test_a_test_that_only_calls_the_change_is_rejected_with_or_without_the_tests_of_each_line(
    repo: Repo, tmp_path: Path, with_lines: bool
) -> None:
    tool = adapter() if with_lines else CSharpAdapter()

    result = judge(repo, tmp_path, TEST_THAT_ONLY_CALLS, tool, line_tests=True)

    assert isinstance(result, Rejected)
    assert result.findings[0].measured == 0.0
    assert any(f.target == "src/Calc/Calc.cs:12" for f in result.findings)
