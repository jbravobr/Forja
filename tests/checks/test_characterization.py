"""Tests written before a refactoring are judged as tests of code that exists, and the planner is
told which functions the tests do not protect."""

import asyncio
from typing import Any

import pytest

from forja.core.artifacts.models import Producer
from forja.core.gates import Verdict
from forja.orchestrator.ports import AttemptContext, TaskInfo
from tests.checks.support import Scene, sinks
from tests.checks.test_measurer import findings, ran
from tests.core.idempotency.conftest import Repo

MOD = "src/sample/mod.py"
THREE = (
    "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
    "def sub(a: int, b: int) -> int:\n    return a - b\n\n\n"
    "def mul(a: int, b: int) -> int:\n    return a * b\n"
)
TESTS_OF_TWO = (
    "from sample.mod import add, sub\n\n\n"
    "def test_add() -> None:\n    assert add(1, 2) == 3\n\n\n"
    "def test_sub() -> None:\n    sub(3, 1)\n"
)
BOTH = (
    "def both(a: int, b: int) -> tuple[int, int]:\n    x = a + b\n    y = a - b\n    return x, y\n"
)
TEST_OF_HALF = (
    "from sample.mod import both\n\n\ndef test_both() -> None:\n    assert both(3, 1)[0] == 4\n"
)


def author(scene: Scene, **more: Any) -> AttemptContext:
    task = TaskInfo(
        "t1",
        "t1-t",
        "before the change: reduce the complexity of add",
        (MOD,),
        8,
        400,
        role=Producer.TEST_AUTHOR,
        test_root="tests",
        attempt_prefix="t1-ta",
        **more,
    )
    return AttemptContext(task, "t1-ta1", scene.repo.path, None)


def test_tests_written_before_a_refactoring_pass_on_the_code_as_it_is(scene: Scene) -> None:
    scene.write("tests/test_more.py", "def test_more() -> None:\n    assert True\n")

    outcome = asyncio.run(scene.measurer.judge(author(scene, characterizes=True), sinks))

    assert outcome.verdict is Verdict.PASSED
    assert "tests" in ran(outcome) and "red_green" not in ran(outcome)


def test_a_test_written_before_a_refactoring_that_fails_is_rejected(scene: Scene) -> None:
    scene.write("tests/test_more.py", "def test_more() -> None:  # FAILS\n    assert False\n")

    outcome = asyncio.run(scene.measurer.judge(author(scene, characterizes=True), sinks))

    assert ran(outcome)[-1] == "tests"
    assert findings(outcome) == ["test failed [tests/test_more.py::test_more]"]


def weak(scene: Scene, *names: str) -> set[str]:
    asked = [(MOD, name) for name in names]
    found = asyncio.run(scene.measurer.weak_nets(scene.repo.path, sinks, asked))
    assert all(file == MOD for file, _ in found)
    return {name for _, name in found}


def test_nothing_asked_is_nothing_measured(scene: Scene) -> None:
    assert weak(scene) == set()
    assert scene.recorder.runs == []


def test_a_function_whose_tests_cannot_be_measured_is_not_called_weak(scene: Scene) -> None:
    # the stand-in for pytest writes no coverage report
    assert weak(scene, "add") == set()


def test_a_function_the_measure_of_complexity_does_not_know_is_not_called_weak(
    scene: Scene,
) -> None:
    assert weak(scene, "gone") == set()


@pytest.mark.slow
def test_a_function_is_weak_when_a_line_has_no_test_or_a_change_goes_unnoticed(
    repo: Repo, tmp_path: Any
) -> None:
    pytest.importorskip("coverage")
    scene = Scene(repo, tmp_path, real=True)
    scene.write(MOD, THREE)
    scene.write("tests/test_mod.py", TESTS_OF_TWO)

    # add is checked; sub is run and not checked; no test runs mul
    assert weak(scene, "add", "sub", "mul") == {"sub", "mul"}
    assert weak(scene, "add") == set()


@pytest.mark.slow
def test_the_limit_of_the_score_is_the_one_of_the_policy(repo: Repo, tmp_path: Any) -> None:
    pytest.importorskip("coverage")
    scene = Scene(repo, tmp_path, real=True)
    scene.write(MOD, BOTH)
    scene.write("tests/test_mod.py", TEST_OF_HALF)

    assert weak(scene, "both") == {"both"}

    loose = {"mutation": {"thresholds": {"python": {"min_score": 0.2}}}}
    scene.measurer = scene.make_measurer(values=loose)

    assert weak(scene, "both") == set()
