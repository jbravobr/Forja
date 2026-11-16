"""A project made by the C# template measures its coverage, and the mutation gate judges a change
to it with the tests of each line (the .NET SDK and dotnet-coverage for real)."""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from forja.checks.changes import read_changes
from forja.checks.mutation import MutationCheck
from forja.checks.tooling import Tools
from forja.core.adapters import CommandPlan, Operation, OperationRequest
from forja.core.events import EventLog
from forja.core.gates import GateInput
from forja.core.gates.models import Approved, GateResult
from forja.core.states import ExecutionState, Level
from forja.languages.csharp import CSharpAdapter
from forja.languages.csharp.adapter import DOTNET_ENVIRONMENT, find_dotnet_coverage
from forja.languages.csharp.template import template
from tests.checks.support import RUN, sinks
from tests.core.events.support import FakeClock
from tests.core.idempotency.conftest import Repo
from tests.languages.csharp.test_mutation import write
from tests.languages.csharp.test_test_lines import execute

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("dotnet") is None, reason="the .NET SDK is not installed"),
]

COVERAGE = find_dotnet_coverage(os.environ)
needs_coverage = pytest.mark.skipif(COVERAGE is None, reason="dotnet-coverage is not installed")
TWICE = "    public static int Twice(int x)\n    {\n        return x * 2;\n    }\n}\n"
TWICE_TESTS = (
    "using MyTool;\nusing Xunit;\n\nnamespace MyTool.Tests;\n\npublic class TwiceTests\n{\n"
    "    [Fact]\n    public void Doubles()\n    {\n"
    "        Assert.Equal(4, MyTool.Version.Twice(2));\n    }\n}\n"
)


def project(root: Path) -> None:
    for relative, text in template("my-tool").files.items():
        write(root, relative, text)


def adapter() -> CSharpAdapter:
    assert COVERAGE is not None
    return CSharpAdapter(dotnet_coverage=COVERAGE)


@needs_coverage
def test_a_project_made_by_the_template_measures_its_coverage(tmp_path: Path) -> None:
    project(tmp_path)
    plan = CSharpAdapter().plan(Operation.COVERAGE, OperationRequest(root=tmp_path))
    assert isinstance(plan, CommandPlan)

    assert execute(plan, tmp_path / "coverage.log") == 0
    reports = list((tmp_path / ".forja-coverage").glob("*/coverage.cobertura.xml"))
    assert len(reports) == 1


@needs_coverage
def test_the_mutation_gate_approves_a_new_method_the_template_tests_check(
    repo: Repo, tmp_path: Path
) -> None:
    project(repo.path)
    repo.sh("add", "-A")
    repo.sh("commit", "-m", "the template")
    source = repo.path / "src/MyTool/Version.cs"
    text = source.read_text(encoding="utf-8")
    source.write_text(text.rstrip()[: -len("}")] + TWICE, encoding="utf-8", newline="\n")
    write(repo.path, "tests/MyTool.Tests/TwiceTests.cs", TWICE_TESTS)
    history = tmp_path / "history.json"

    result = judge(repo, tmp_path, adapter(), history)

    assert isinstance(result, Approved)
    assert "MyTool.Tests.TwiceTests.Doubles" in history.read_text(encoding="utf-8")


def judge(repo: Repo, tmp_path: Path, tool: CSharpAdapter, history: Path) -> GateResult:
    log = EventLog.open(tmp_path / "events.jsonl", RUN, clock=FakeClock())
    log.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)
    wanted = {name.upper() for name in DOTNET_ENVIRONMENT} | {
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "PATHEXT",
    }
    host_env = {**repo.env, **{k: v for k, v in os.environ.items() if k.upper() in wanted}}
    tools = Tools(
        sinks=sinks,
        log=log,
        level=Level.ATTEMPT,
        target="a",
        logs=tmp_path / "tool-logs",
        host_env=host_env,
    )
    changes = asyncio.run(read_changes(repo.git.at(repo.path)))
    check = MutationCheck(
        tools,
        tool,
        repo.path,
        changes,
        ("src/MyTool/Version.cs",),
        host_env,
        language="csharp",
        history=history,
    )
    # the tests of the lines first, as the policy may ask: the history names the test that killed
    values: dict[str, object] = {"limits": {"line_tests": 1}}
    data = GateInput(Path("."), "c" * 40, "t", "s", "a", values=values)  # type: ignore[arg-type]
    return asyncio.run(check.evaluate(data))
