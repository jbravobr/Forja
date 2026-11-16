"""The line tests and the filtered run with the real Go tool (``slow``).

The module has one function two test files and a table of subtests call, and one function no test
calls. The tests of a line come from the line driver, read by the core, and given back to the
filtered run as ids.
"""

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from forja.checks.changes import read_changes
from forja.checks.mutation import HISTORY_VERSION, MutationCheck, failed_test
from forja.checks.tooling import Tools
from forja.core.adapters import CommandPlan, Operation, OperationRequest, ToolOutput
from forja.core.events import EventLog
from forja.core.gates import GateInput
from forja.core.gates.models import Approved, Rejected
from forja.core.reports import ReportFormat, ReportSpec
from forja.core.reports.formats import TEST_LINES_DIRECTORY
from forja.core.reports.readers import read_test_lines
from forja.core.states import ExecutionState, Level
from forja.languages.go import GoAdapter
from forja.languages.go.adapter import GO_ENVIRONMENT
from tests.checks.support import RUN, sinks
from tests.core.events.support import FakeClock
from tests.core.idempotency.conftest import Repo

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("go") is None, reason="the Go tool chain is not installed"),
]

MODULE = "example.com/calc"
CALC = (
    "package calc\n\n"
    "func Add(a, b int) int {\n\treturn a + b\n}\n\n"
    "func Sub(a, b int) int {\n\treturn a - b\n}\n"
)
TEST_ADD = (
    'package calc\n\nimport "testing"\n\n'
    'func TestAdd(t *testing.T) {\n\tif Add(1, 2) != 3 {\n\t\tt.Fatal("add")\n\t}\n}\n\n'
    "func TestTable(t *testing.T) {\n"
    '\tt.Run("zero", func(t *testing.T) {\n'
    '\t\tif Add(0, 0) != 0 {\n\t\t\tt.Fatal("zero")\n\t\t}\n\t})\n'
    "}\n"
)
TEST_AGAIN = (
    'package calc\n\nimport "testing"\n\n'
    'func TestAddAgain(t *testing.T) {\n\tif Add(2, 2) != 4 {\n\t\tt.Fatal("again")\n\t}\n}\n'
)
ADD_LINE = 4  # the body of Add in calc.go
SUB_LINE = 8  # the body of Sub: no test calls it


@pytest.fixture
def module(tmp_path: Path) -> Path:
    root = tmp_path / "module"
    root.mkdir()
    files = {
        "go.mod": f"module {MODULE}\n\ngo 1.22\n",
        "calc.go": CALC,
        "calc_test.go": TEST_ADD,
        "again_test.go": TEST_AGAIN,
    }
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8", newline="\n")
    return root


def run(plan: CommandPlan, log: Path) -> ToolOutput:
    done = subprocess.run(
        list(plan.argv), cwd=plan.cwd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )  # fmt: skip
    log.write_text(done.stdout, encoding="utf-8")
    return ToolOutput(done.returncode, log, plan.cwd)


def line_tests(module: Path) -> dict[str, dict[int, frozenset[str]]]:
    adapter = GoAdapter()
    plan = adapter.plan(Operation.TEST_LINES, OperationRequest(root=module))
    assert isinstance(plan, CommandPlan), plan
    done = subprocess.run(list(plan.argv), cwd=plan.cwd, check=False, capture_output=True)
    assert done.returncode == 0, done.stderr
    spec = adapter.report(Operation.TEST_LINES)
    assert isinstance(spec, ReportSpec) and spec.index is not None
    directory = module / TEST_LINES_DIRECTORY
    texts = {p.name: p.read_text(encoding="utf-8") for p in directory.glob("*.out")}
    index = (module / spec.index).read_text(encoding="utf-8")
    found = read_test_lines(ReportFormat.GO_COVERPROFILE, texts, module, (), index)
    shutil.rmtree(directory, ignore_errors=True)
    return {path: dict(lines) for path, lines in found.items()}


def test_a_line_is_run_by_the_tests_of_both_files_and_a_line_without_a_test_by_none(
    module: Path,
) -> None:
    found = line_tests(module)["calc.go"]

    assert found[ADD_LINE] == frozenset(
        {f"{MODULE}/TestAdd", f"{MODULE}/TestAddAgain", f"{MODULE}/TestTable/zero"}
    )
    assert SUB_LINE not in found


def test_the_ids_of_a_line_run_exactly_those_tests(module: Path, tmp_path: Path) -> None:
    ids = tuple(sorted(line_tests(module)["calc.go"][ADD_LINE]))
    adapter = GoAdapter()
    plan = adapter.plan(Operation.TEST, OperationRequest(root=module, tests=ids, rerun=True))
    assert isinstance(plan, CommandPlan), plan

    output = run(plan, tmp_path / "out.log")
    parsed = adapter.parse(Operation.TEST, output)

    assert output.exit_code == 0
    ran = {d.target.id for d in getattr(parsed, "drafts", ()) if d.measurement == "test.outcome"}
    # the parent of the subtest reports its own outcome too; nothing else runs
    assert set(ids) <= ran <= set(ids) | {f"{MODULE}/TestTable"}


def test_the_stop_is_at_the_first_test_that_fails_and_it_is_named(
    module: Path, tmp_path: Path
) -> None:
    (module / "calc.go").write_text(CALC.replace("a + b", "a + b + 1"), encoding="utf-8")
    adapter = GoAdapter()
    ids = (f"{MODULE}/TestAddAgain", f"{MODULE}/TestAdd", f"{MODULE}/TestTable/zero")
    plan = adapter.plan(Operation.TEST, OperationRequest(root=module, tests=ids, rerun=True))
    assert isinstance(plan, CommandPlan), plan

    output = run(plan, tmp_path / "out.log")

    # the files of a package run in the order of their names: again_test.go first, and it fails;
    # the run stops there, so TestAdd, asked for too, never runs
    assert output.exit_code != 0
    assert failed_test(adapter, output, ids) == f"{MODULE}/TestAddAgain"
    assert '"Test":"TestAdd"' not in output.log_path.read_text(encoding="utf-8")


SUB_BASE = {
    "go.mod": f"module {MODULE}\n\ngo 1.22\n",
    "calc.go": "package calc\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n",
    "calc_test.go": TEST_ADD,
}
SUB_CHANGE = {
    "calc.go": CALC,
    "again_test.go": TEST_AGAIN
    + '\nfunc TestSubChecks(t *testing.T) {\n\tif Sub(3, 1) != 2 {\n\t\tt.Fatal("sub")\n\t}\n}\n',
}


def test_the_test_that_kills_a_mutant_is_kept_in_the_history_and_nothing_else_is(
    repo: Repo, tmp_path: Path
) -> None:
    """End to end: the mutant of ``Sub`` is killed by ``TestSubChecks`` alone, so the history names
    that test, and the verdict is the one a run without line tests gives."""
    for name, text in SUB_BASE.items():
        (repo.path / name).parent.mkdir(parents=True, exist_ok=True)
        (repo.path / name).write_text(text, encoding="utf-8", newline="\n")
    repo.sh("add", "-A")
    repo.sh("commit", "-m", "module")
    for name, text in SUB_CHANGE.items():
        (repo.path / name).write_text(text, encoding="utf-8", newline="\n")
    history = tmp_path / "killers.json"

    log = EventLog.open(tmp_path / "events.jsonl", RUN, clock=FakeClock())
    log.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)
    wanted = GO_ENVIRONMENT | {"TEMP", "TMP", "USERPROFILE"}
    host_env = {**repo.env, **{k: v for k, v in os.environ.items() if k.upper() in wanted}}
    tools = Tools(
        sinks=sinks, log=log, level=Level.ATTEMPT, target="a",
        logs=tmp_path / "tool-logs", host_env=host_env,
    )  # fmt: skip
    changes = asyncio.run(read_changes(repo.git.at(repo.path)))
    check = MutationCheck(
        tools, GoAdapter(), repo.path, changes, ("calc.go",), host_env,
        language="go", history=history,
    )  # fmt: skip
    data = GateInput(Path("."), "c" * 40, "t", "s", "a", values={})

    result = asyncio.run(check.evaluate(data))

    assert isinstance(result, Rejected | Approved), result
    document = json.loads(history.read_text(encoding="utf-8"))
    assert document["version"] == HISTORY_VERSION
    assert set(document["killers"].values()) == {f"{MODULE}/TestSubChecks"}
