"""The test author's gate: the declared tests fail, everything else keeps its verdict."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from forja.checks.red_green import RedGreenCheck
from forja.core.adapters import FileClass
from forja.core.artifacts.llm_output import DeclaredTest
from forja.core.events import EventLog
from forja.core.gates import Approved, Rejected
from forja.core.states import ExecutionState, Level
from forja.languages.go import GoAdapter
from forja.languages.python import PythonAdapter
from tests.checks.support import RUN
from tests.checks.test_units import data, tools
from tests.core.events.support import FakeClock

ADAPTER = PythonAdapter()


@pytest.fixture
def log(tmp_path: Path) -> EventLog:
    opened = EventLog.open(tmp_path / "events.jsonl", RUN, clock=FakeClock())
    opened.append_transition(Level.EXECUTION, RUN, None, ExecutionState.PREPARING)
    return opened


NEW = "tests/test_new.py::test_page_one"
OLD = "tests/test_old.py::test_old"


def classify(path: str) -> FileClass:
    return ADAPTER.classify(path) or FileClass.OTHER


def declared(*ids: str) -> tuple[DeclaredTest, ...]:
    return tuple(
        DeclaredTest(criterion_id=n, test_id=test_id, path=test_id.split("::")[0])
        for n, test_id in enumerate(ids, start=1)
    )


def run(
    log: EventLog,
    tmp_path: Path,
    lines: list[str],
    *,
    tests: tuple[DeclaredTest, ...] | None = None,
    criteria: frozenset[int] = frozenset({1}),
    passing: frozenset[str] = frozenset(),
) -> Any:
    (tmp_path / "tests").mkdir(exist_ok=True)
    for name in ("test_new.py", "test_old.py"):
        (tmp_path / "tests" / name).write_text("", encoding="utf-8")
    failing = any(line.startswith(("FAILED", "ERROR")) for line in lines)
    script = "print(" + repr("\n".join(lines)) + f")\nraise SystemExit({int(failing)})"
    values = {"commands": {"test": [sys.executable, "-c", script]}}
    check = RedGreenCheck(
        tools(log, tmp_path),
        (ADAPTER,),
        tmp_path,
        classify,
        criteria,
        declared(NEW) if tests is None else tests,
        passing=passing,
    )
    return asyncio.run(check.evaluate(data(values)))


def described(result: object) -> list[str]:
    assert isinstance(result, Rejected)
    return [f.describe() for f in result.findings]


def test_a_declared_test_that_fails_and_an_old_one_that_passes_are_approved(
    log: EventLog, tmp_path: Path
) -> None:
    result = run(
        log,
        tmp_path,
        [f"FAILED {NEW} - assert 1 == 2", f"PASSED {OLD}"],
        passing=frozenset({OLD}),
    )

    assert result == Approved()


def test_a_declared_test_that_passes_on_the_defect_is_named(log: EventLog, tmp_path: Path) -> None:
    result = run(log, tmp_path, [f"PASSED {NEW}"])

    assert described(result) == [f"the declared test passes on the code with the defect [{NEW}]"]


def test_a_declared_test_that_errors_is_not_a_red_test(log: EventLog, tmp_path: Path) -> None:
    result = run(log, tmp_path, [f"ERROR {NEW} - ImportError"])

    assert described(result) == [
        f"the declared test errors out instead of failing (import or collection) [{NEW}]"
    ]


def test_a_declared_test_that_fails_on_a_name_nobody_defined_is_not_a_red_test(
    log: EventLog, tmp_path: Path
) -> None:
    result = run(log, tmp_path, [f"FAILED {NEW} - NameError: name 'page' is not defined"])

    assert described(result) == [
        "the declared test fails on its own mistake (NameError), "
        f"which no change to the code can repair [{NEW}]"
    ]


@pytest.mark.parametrize(
    "why",
    [
        "AttributeError: module 'sample.mod' has no attribute 'page'",  # the code to be written
        "AssertionError",
        "sample.NameErrorLike: x",
    ],
)
def test_a_declared_test_that_fails_on_what_the_code_lacks_is_a_red_test(
    log: EventLog, tmp_path: Path, why: str
) -> None:
    assert run(log, tmp_path, [f"FAILED {NEW} - {why}"]) == Approved()


def test_a_name_nobody_defined_in_an_undeclared_test_is_one_failure_like_any_other(
    log: EventLog, tmp_path: Path
) -> None:
    lines = [f"FAILED {NEW} - assert 1 == 2", f"FAILED {OLD} - NameError: name 'x' is not defined"]

    assert described(run(log, tmp_path, lines)) == [f"test failed [{OLD}]"]


def test_a_declared_test_that_did_not_run_is_named(log: EventLog, tmp_path: Path) -> None:
    result = run(log, tmp_path, [f"PASSED {OLD}"], passing=frozenset({OLD}))

    assert described(result) == [f"the declared test did not run [{NEW}]"]


def test_an_error_of_collection_in_another_file_rejects(log: EventLog, tmp_path: Path) -> None:
    result = run(log, tmp_path, [f"FAILED {NEW}", "ERROR tests/test_old.py"])

    assert described(result) == ["test error [tests/test_old.py]"]


def test_an_old_test_that_stops_passing_is_rejected_once(log: EventLog, tmp_path: Path) -> None:
    result = run(log, tmp_path, [f"FAILED {NEW}", f"FAILED {OLD}"], passing=frozenset({OLD}))

    assert described(result) == [f"test failed [{OLD}]"]


def test_an_undeclared_test_that_fails_is_rejected(log: EventLog, tmp_path: Path) -> None:
    result = run(log, tmp_path, [f"FAILED {NEW}", "FAILED tests/test_new.py::test_extra"])

    assert described(result) == ["test failed [tests/test_new.py::test_extra]"]


def test_a_criterion_without_a_test_is_named(log: EventLog, tmp_path: Path) -> None:
    result = run(log, tmp_path, [f"FAILED {NEW}"], criteria=frozenset({1, 2}))

    assert described(result) == ["no test is declared for this criterion [criterion 2]"]


def test_a_test_for_a_criterion_the_task_does_not_have_is_named(
    log: EventLog, tmp_path: Path
) -> None:
    result = run(log, tmp_path, [f"FAILED {NEW}"], criteria=frozenset({2}))

    assert described(result) == [
        "no test is declared for this criterion [criterion 2]",
        f"the declared test covers no criterion of the task [{NEW}]",
    ]


@pytest.mark.parametrize(
    ("test_id", "message"),
    [
        ("tests/missing.py::test_x", "the declared test file does not exist"),
        ("src/pkg/mod.py::test_x", "the declared file is not a test file"),
    ],
)
def test_a_declared_file_must_be_an_existing_test_file(
    log: EventLog, tmp_path: Path, test_id: str, message: str
) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.py").write_text("", encoding="utf-8")

    result = run(log, tmp_path, [f"FAILED {NEW}"], tests=declared(test_id))

    assert described(result) == [f"{message} [{test_id.split('::')[0]}]"]


def test_a_test_id_must_belong_to_its_file(log: EventLog, tmp_path: Path) -> None:
    wrong = DeclaredTest(
        criterion_id=1, test_id="tests/test_old.py::test_x", path="tests/test_new.py"
    )

    result = run(log, tmp_path, [f"FAILED {NEW}"], tests=(wrong,))

    assert described(result) == [
        "the test id does not belong to tests/test_new.py [tests/test_old.py::test_x]"
    ]


def test_an_id_may_not_carry_the_path_where_the_language_runner_does_not_print_it(
    tmp_path: Path,
) -> None:
    def check_of(adapter: Any) -> RedGreenCheck:
        return RedGreenCheck(None, (adapter,), tmp_path, classify, frozenset({1}), ())  # type: ignore[arg-type]

    go = DeclaredTest(criterion_id=1, test_id="example.com/calc/TestAdd", path="calc_add_test.go")
    py = DeclaredTest(criterion_id=1, test_id="example.com/calc/TestAdd", path="tests/test_a.py")

    (tmp_path / "calc_add_test.go").write_text("func TestAdd(t *testing.T) {}", encoding="utf-8")

    assert check_of(GoAdapter())._owns(go)
    assert not check_of(PythonAdapter())._owns(py)
