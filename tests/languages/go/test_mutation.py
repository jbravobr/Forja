"""Mutation of Go code, for real: ``go test`` runs on copies changed by the mutator's rules.

Needs the Go tool chain; the tests are skipped without it.
"""

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from forja.checks.changes import read_changes
from forja.checks.mutation import MutationCheck
from forja.checks.tooling import Tools
from forja.core.adapters import CommandPlan, NotSupported, Operation, OperationRequest
from forja.core.events import EventLog
from forja.core.gates import GateInput
from forja.core.gates.models import Approved, GateResult, Rejected
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

BASE = {
    "go.mod": "module example.com/calc\n\ngo 1.22\n",
    "calc.go": "package calc\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n",
    "calc_test.go": (
        'package calc\n\nimport "testing"\n\n'
        "func TestAdd(t *testing.T) {\n"
        '\tif Add(2, 3) != 5 {\n\t\tt.Fatal("add")\n\t}\n}\n'
    ),
}
WITH_SUB = BASE["calc.go"] + "\nfunc Sub(a, b int) int {\n\treturn a - b\n}\n"
TEST_ADD = BASE["calc_test.go"]
TEST_THAT_CHECKS = TEST_ADD + (
    '\nfunc TestSub(t *testing.T) {\n\tif Sub(3, 1) != 2 {\n\t\tt.Fatal("sub")\n\t}\n}\n'
)
TEST_THAT_ONLY_CALLS = TEST_ADD + "\nfunc TestSub(t *testing.T) {\n\tSub(3, 1)\n}\n"
TEST_THAT_IS_EMPTY = TEST_ADD + "\nfunc TestSub(t *testing.T) {\n}\n"


def write(repo: Repo, files: dict[str, str]) -> None:
    for name, text in files.items():
        (repo.path / name).parent.mkdir(parents=True, exist_ok=True)
        (repo.path / name).write_text(text, encoding="utf-8", newline="\n")


def judge(repo: Repo, tmp_path: Path, test_text: str) -> GateResult:
    """Commit the base module, then add ``Sub`` with the given test file and judge the change."""
    change = {"calc.go": WITH_SUB, "calc_test.go": test_text}
    return judge_change(repo, tmp_path, BASE, change, "calc.go", GoAdapter())


def judge_change(
    repo: Repo,
    tmp_path: Path,
    base: dict[str, str],
    change: dict[str, str],
    changed: str,
    adapter: GoAdapter,
    line_tests: bool = False,
) -> GateResult:
    """Commit ``base``, then write ``change`` over it and judge the lines ``changed`` gained: by
    the package of the file first, or with ``line_tests`` by the tests of the lines first, as the
    policy may ask."""
    write(repo, base)
    repo.sh("add", "-A")
    repo.sh("commit", "-m", "module")
    write(repo, change)

    log = EventLog.open(tmp_path / "events.jsonl", RUN, clock=FakeClock())
    log.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)
    wanted = GO_ENVIRONMENT | {"TEMP", "TMP", "USERPROFILE"}  # what a real run inherits
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
        adapter,
        repo.path,
        changes,
        (changed,),
        host_env,
        language="go",
    )
    values = {"limits": {"line_tests": 1}} if line_tests else {}
    data = GateInput(Path("."), "c" * 40, "t", "s", "a", values=values)  # type: ignore[arg-type]
    return asyncio.run(check.evaluate(data))


def test_a_new_function_the_tests_check_is_approved(repo: Repo, tmp_path: Path) -> None:
    assert isinstance(judge(repo, tmp_path, TEST_THAT_CHECKS), Approved)


@pytest.mark.parametrize("test_text", [TEST_THAT_ONLY_CALLS, TEST_THAT_IS_EMPTY])
def test_a_test_that_checks_nothing_is_rejected(repo: Repo, tmp_path: Path, test_text: str) -> None:
    result = judge(repo, tmp_path, test_text)

    assert isinstance(result, Rejected)
    if test_text is TEST_THAT_ONLY_CALLS:  # the line runs, and no change to it is noticed
        assert result.findings[0].measured == 0.0
        assert result.findings[0].limit == 0.70
        assert any(f.target == "calc.go:8" for f in result.findings)
    else:  # no test runs the new line at all
        # the body and the closing brace the profile counts in the same block
        assert {f.target for f in result.findings} == {"calc.go:8", "calc.go:9"}
        assert "no test runs this new line" in result.findings[0].message


# -- a module of two packages: the package of the file judges first, every package after it --------

TWO_PACKAGES = {
    "go.mod": BASE["go.mod"],
    "calc/calc.go": BASE["calc.go"],
    "calc/calc_test.go": BASE["calc_test.go"],
    "uses/uses.go": "package uses\n",
}
USES = 'package uses\n\nimport (\n\t"testing"\n\n\t"example.com/calc/calc"\n)\n\n'
OTHER_PACKAGE_CHECKS = USES + (
    'func TestSub(t *testing.T) {\n\tif calc.Sub(3, 1) != 2 {\n\t\tt.Fatal("sub")\n\t}\n}\n'
)
OTHER_PACKAGE_ONLY_CALLS = USES + "func TestSub(t *testing.T) {\n\tcalc.Sub(3, 1)\n}\n"


class Listening(GoAdapter):
    """The Go adapter, noting the file each run of the tests was said to be for."""

    def __init__(self) -> None:
        super().__init__()
        self.runs: list[tuple[tuple[str, ...], str]] = []

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan | NotSupported:
        answer = super().plan(operation, request)
        # a run in a copy: the runs in the repository only ask which command a file would get
        if operation is Operation.TEST and "worker-" in request.root.name:
            assert isinstance(answer, CommandPlan)
            # the tests asked for, and the package the run is for ("./..." for the whole command)
            self.runs.append((request.tests, answer.argv[-1]))
        return answer


PACKAGE = ((), "./calc")  # the package of the file, with no test chosen
EVERY_PACKAGE = ((), "./...")
BOTH_WAYS = pytest.mark.parametrize("line_tests", [False, True], ids=["package", "line tests"])


@BOTH_WAYS
def test_a_line_only_a_test_of_another_package_checks_is_judged_by_every_package(
    repo: Repo, tmp_path: Path, line_tests: bool
) -> None:
    """``Sub`` is new in ``calc`` and only ``uses`` has a test for it. Coverage is measured over
    every package, so the line counts as run. Left alone, the tests of ``calc`` let its mutant
    live; it only counts once every package has run, and there it is killed. With the tests of
    the lines, the test of ``uses`` that runs the line kills the mutant in its own run."""
    adapter = Listening()
    change = {"calc/calc.go": WITH_SUB, "uses/uses_test.go": OTHER_PACKAGE_CHECKS}

    result = judge_change(repo, tmp_path, TWO_PACKAGES, change, "calc/calc.go", adapter, line_tests)

    assert isinstance(result, Approved)
    if line_tests:
        # the test of the line runs on the unchanged copy first (it passes, so it judges), then
        # the mutant: the test kills it, and the whole command is never run
        uses = (("example.com/calc/uses/TestSub",), "./uses")
        assert adapter.runs == [uses, uses]
    else:
        # the package on the unchanged copy and on the mutant, then every package on both
        assert adapter.runs == [PACKAGE, PACKAGE, EVERY_PACKAGE, EVERY_PACKAGE]


@BOTH_WAYS
def test_a_line_no_package_checks_survives_its_package_and_every_package(
    repo: Repo, tmp_path: Path, line_tests: bool
) -> None:
    adapter = Listening()
    change = {"calc/calc.go": WITH_SUB, "uses/uses_test.go": OTHER_PACKAGE_ONLY_CALLS}

    result = judge_change(repo, tmp_path, TWO_PACKAGES, change, "calc/calc.go", adapter, line_tests)

    assert isinstance(result, Rejected)
    assert result.findings[0].measured == 0.0
    assert any(f.target == "calc/calc.go:8" for f in result.findings)
    # whoever judges first passes on the unchanged copy and lets the mutant live; only then does
    # the whole command get its baseline and judge the mutant
    first = (("example.com/calc/uses/TestSub",), "./uses") if line_tests else PACKAGE
    assert adapter.runs == [first, first, EVERY_PACKAGE, EVERY_PACKAGE]


@BOTH_WAYS
def test_a_line_its_own_package_checks_never_waits_for_the_other_packages(
    repo: Repo, tmp_path: Path, line_tests: bool
) -> None:
    adapter = Listening()
    change = {"calc/calc.go": WITH_SUB, "calc/calc_test.go": TEST_THAT_CHECKS}

    result = judge_change(repo, tmp_path, TWO_PACKAGES, change, "calc/calc.go", adapter, line_tests)

    assert isinstance(result, Approved)
    # in its own package, on the unchanged copy and then on the mutant: the test of the line, or
    # every test of the package
    own = (("example.com/calc/calc/TestSub",), "./calc") if line_tests else PACKAGE
    assert adapter.runs == [own, own]
