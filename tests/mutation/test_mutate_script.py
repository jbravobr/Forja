"""The mutation gate's own script: which tests judge a mutant, and that the verdict stays the one
every test of the file would give."""

import asyncio
import importlib.util
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from forja.core.reports.models import MutantResult, MutantStatus, MutationReport
from forja.mutation.executor import Mutant, TestVerdict
from tests.core.process.support import LOWEST_PRIORITY, OWN_PRIORITY

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mutate.py"


@pytest.fixture(scope="module")
def mutate() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("forja_mutate_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


@pytest.fixture(autouse=True)
def the_test_process_keeps_its_priority(mutate: Any, monkeypatch: Any) -> None:
    """``main`` lowers the priority of the process it runs in, and here that is the test run."""
    monkeypatch.setattr(mutate, "yield_the_machine", lambda: None)


# -- the rest of the machine comes first -----------------------------------------------------------


def test_the_script_and_what_it_starts_run_at_the_lowest_priority_of_the_system() -> None:
    code = (
        "import importlib.util, subprocess, sys\n"
        f"spec = importlib.util.spec_from_file_location('forja_mutate_script', {str(SCRIPT)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "module.yield_the_machine()\n"
        f"exec({OWN_PRIORITY!r})\n"
        f"child = subprocess.run([sys.executable, '-c', {OWN_PRIORITY!r}], capture_output=True)\n"
        "print(child.stdout.decode().strip())\n"
    )

    ran = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)

    assert ran.stdout.split() == [str(LOWEST_PRIORITY), str(LOWEST_PRIORITY)], ran.stderr


def test_the_run_yields_the_machine_before_it_starts_anything(
    mutate: Any, monkeypatch: Any
) -> None:
    order: list[str] = []

    def canary() -> bool:
        order.append("canary")
        return False

    monkeypatch.setattr(mutate, "yield_the_machine", lambda: order.append("yield"))
    monkeypatch.setattr(mutate, "canary", canary)
    monkeypatch.setattr(sys, "argv", ["mutate.py", "src/calc.py"])

    assert mutate.main() == 4
    assert order == ["yield", "canary"]


# -- the command line ------------------------------------------------------------------------------


def test_the_ceiling_of_runs_at_once_is_asked_for_by_the_name_the_mutator_gives_it(
    mutate: Any, monkeypatch: Any
) -> None:
    assert mutate.parse_args(["--max-workers", "3", "a.py"]).max_workers == 3
    monkeypatch.setattr(mutate.os, "cpu_count", lambda: 32)  # one per processor, as the mutator
    assert mutate.parse_args([]).max_workers == 32
    monkeypatch.setattr(mutate.os, "cpu_count", lambda: 2)
    assert mutate.parse_args([]).max_workers == 2
    monkeypatch.setattr(mutate.os, "cpu_count", lambda: None)
    assert mutate.parse_args([]).max_workers == 1
    with pytest.raises(SystemExit):
        mutate.parse_args(["--workers", "3"])


# -- the test files dealt into runs that go at the same time ---------------------------------------


def test_the_test_files_are_dealt_into_hands_and_none_is_left_out(mutate: Any) -> None:
    files = [f"tests/test_{n}.py" for n in range(7)]

    hands = mutate.shards(files, 3)

    assert hands == [
        ["tests/test_0.py", "tests/test_3.py", "tests/test_6.py"],
        ["tests/test_1.py", "tests/test_4.py"],
        ["tests/test_2.py", "tests/test_5.py"],
    ]
    assert sorted(file for hand in hands for file in hand) == sorted(files)


def test_each_hand_is_run_in_the_order_of_the_names_whatever_order_the_files_came_in(
    mutate: Any,
) -> None:
    by_size = ["tests/z.py", "tests/core/b.py", "tests/a.py", "tests/core/a.py", "tests/m.py"]

    assert mutate.shards(by_size, 2) == [
        ["tests/a.py", "tests/m.py", "tests/z.py"],
        ["tests/core/a.py", "tests/core/b.py"],
    ]


def test_fewer_files_than_runs_leave_no_empty_hand_and_a_count_of_zero_is_one_run(
    mutate: Any,
) -> None:
    assert mutate.shards(["a", "b"], 8) == [["a"], ["b"]]
    assert mutate.shards(["a", "b"], 0) == [["a", "b"]]
    assert mutate.shards([], 4) == []


def test_every_test_file_of_the_project_is_found_once(mutate: Any) -> None:
    found = mutate.all_test_files()

    assert "tests/mutation/test_mutate_script.py" in found
    assert len(found) == len(set(found))
    assert all(Path(name).name.startswith("test_") for name in found)
    assert "tests/conftest.py" not in found


# -- the tests of a line ---------------------------------------------------------------------------


def test_each_line_gets_the_test_files_that_ran_it(mutate: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(mutate, "test_file", lambda context: context or None)
    files = [
        (
            "src\\forja\\nowhere.py",
            {3: ["tests/a.py", ""], 4: ["tests/a.py", "tests/b.py"], 9: [""]},
        )
    ]

    by_line = mutate.tests_by_line(files)

    assert by_line == {
        "src/forja/nowhere.py": {3: {"tests/a.py"}, 4: {"tests/a.py", "tests/b.py"}, 9: set()}
    }


def test_the_tests_of_each_line_are_read_from_the_coverage_data_for_the_files_of_the_project(
    mutate: Any, tmp_path: Path
) -> None:
    from coverage import CoverageData  # noqa: PLC0415

    inside, later = mutate.ROOT / "src" / "forja" / "nowhere.py", mutate.ROOT / "src" / "z.py"
    outside = tmp_path / "elsewhere.py"
    data = CoverageData(str(tmp_path / ".coverage"))
    data.set_context("tests.a.test_x")
    data.add_lines({str(later): [1], str(inside): [3, 4], str(outside): [1]})
    data.set_context("tests.b.test_y")
    data.add_lines({str(inside): [4]})
    data.write()

    found = list(mutate.recorded_contexts(tmp_path / ".coverage"))

    assert [name for name, _ in found] == ["src/forja/nowhere.py", "src/z.py"]
    lines = found[0][1]
    assert lines[3] == ["tests.a.test_x"]
    assert sorted(lines[4]) == ["tests.a.test_x", "tests.b.test_y"]


def test_the_other_lines_of_a_statement_take_the_tests_of_its_first(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    source = tmp_path / "m.py"
    source.write_text("x = f(\n    1,\n    2,\n)\ny = 3\n", encoding="utf-8")
    monkeypatch.setattr(mutate, "test_file", lambda context: context or None)

    by_line = mutate.tests_by_line([(str(source), {1: ["tests/a.py"], 5: []})])

    lines = by_line[source.as_posix()]
    assert lines[1] == lines[2] == lines[3] == lines[4] == {"tests/a.py"}
    assert lines[5] == set()


def test_a_mutant_is_judged_by_the_tests_of_its_line_in_the_order_of_the_whole_file(
    mutate: Any,
) -> None:
    everything = ["tests/test_m.py", "tests/a.py", "tests/b.py"]
    by_line = {"src/m.py": {4: {"tests/b.py", "tests/test_m.py"}, 9: set()}}

    assert mutate.tests_of_line(by_line, "src/m.py", 4, everything) == [
        "tests/test_m.py",
        "tests/b.py",
    ]


def test_a_line_no_test_is_named_for_keeps_every_test_of_the_file(mutate: Any) -> None:
    everything = ["tests/a.py", "tests/b.py"]
    by_line: dict[str, dict[int, set[str]]] = {"src/m.py": {9: set()}}

    assert mutate.tests_of_line(by_line, "src/m.py", 9, everything) == everything  # import time
    assert mutate.tests_of_line(by_line, "src/m.py", 77, everything) == everything
    assert mutate.tests_of_line(by_line, "src/other.py", 1, everything) == everything


# -- a run is one process; the unchanged copy of the whole run is spread over several --------------


def started_by(mutate: Any, monkeypatch: Any, runner: Any, root: Path) -> list[str]:
    """The command ``runner`` starts for a run in ``root``."""
    commands: list[list[str]] = []

    class Done:
        pid = 0

        async def wait(self) -> int:
            return 0

    async def start(*command: str, **options: object) -> Done:
        commands.append(list(command))
        return Done()

    monkeypatch.setattr(mutate.asyncio, "create_subprocess_exec", start)
    assert asyncio.run(runner(root, 20.0)) is TestVerdict.PASSED
    (command,) = commands
    return command


def test_a_run_is_one_process_given_its_tests_and_stopped_at_the_first_failure(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    runner = mutate.PytestRunner(["tests/a.py", "tests/b.py"], ["--deselect", "tests/a.py::t"])

    command = started_by(mutate, monkeypatch, runner, tmp_path)

    assert command[-4:] == ["--deselect", "tests/a.py::t", "tests/a.py", "tests/b.py"]
    assert "-x" in command and "-n0" in command


def test_the_run_of_the_whole_command_is_spread_over_the_processes_asked_for(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    command = started_by(mutate, monkeypatch, mutate.PytestRunner(["tests/a.py"], [], 3), tmp_path)

    assert "-n3" in command and "-n0" not in command
    assert mutate.PytestRunner(["tests/a.py"], [], 0).processes == 1


# -- one run on the unchanged copy for the whole command, as in the mutator ------------------------


def test_a_mutant_is_given_ten_times_what_one_process_would_take_on_the_unchanged_copy(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    measured: list[tuple[Path, Path, list[str], list[str], int]] = []

    async def baseline(worktree: Path, copy: Path, runner: Any) -> float:
        measured.append((worktree, copy.parent, runner.tests, runner.deselect, runner.processes))
        assert copy.parent.is_dir() and not copy.exists()
        return 5.0

    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    monkeypatch.setattr(mutate, "baseline", baseline)
    monkeypatch.setattr(mutate, "deselected", lambda: ["--deselect", "x"])

    # five seconds over four processes: one alone takes up to twenty, and ten times that is given
    assert mutate.time_limit(["tests/a.py", "tests/b.py"], 4) == 200.0
    ((worktree, scratch, tests, deselect, processes),) = measured
    assert (worktree, tests, deselect, processes) == (
        tmp_path,
        ["tests/a.py", "tests/b.py"],
        ["--deselect", "x"],
        4,
    )
    assert not scratch.exists()  # the copy is gone with the run
    assert mutate.time_limit(["tests/a.py"], 1) == 50.0
    assert mutate.time_limit(["tests/a.py"], 0) == 50.0


def test_however_fast_the_unchanged_copy_a_mutant_is_given_two_seconds(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    async def baseline(worktree: Path, copy: Path, runner: Any) -> float:
        return 0.01

    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    monkeypatch.setattr(mutate, "baseline", baseline)
    monkeypatch.setattr(mutate, "deselected", lambda: [])

    assert mutate.time_limit(["tests/a.py"], 1) == 2.0


def asking(mutate: Any, tests: list[str], covered: list[bool], kept: set[int]) -> Any:
    mutants = [mutant(place + 1, b"m", covered=runs) for place, runs in enumerate(covered)]
    return mutate.Asked("src/m.py", tests, mutants, [], {}, mutate.NO_HISTORY, {}, kept)


def test_the_unchanged_copy_runs_the_tests_of_every_source_file_with_a_mutant_to_run(
    mutate: Any,
) -> None:
    asked = [
        asking(mutate, ["tests/test_m.py", "tests/b.py"], [True], set()),
        asking(mutate, ["tests/b.py", "tests/a.py"], [True, True], {0}),
        asking(mutate, ["tests/settled.py"], [True, True], {0, 1}),  # the last run killed both
        asking(mutate, ["tests/unreached.py"], [False], set()),  # no test runs its line
    ]

    # once each, in the order of the names
    assert mutate.base_tests(asked) == ["tests/a.py", "tests/b.py", "tests/test_m.py"]
    assert mutate.base_tests(asked[2:]) == []
    assert [len(one.to_run()) for one in asked] == [1, 1, 0, 1]
    assert [m.line for m in asked[1].to_run()] == [2]


# -- a test out of time is a run out of time, not a failed test ------------------------------------


@pytest.mark.parametrize(
    ("said", "code", "verdict"),
    [
        (b"....\r\n+++++++++ Timeout +++++++++\r\nstack of the thread\r\n", 1, "TIMED_OUT"),
        (b"+++++++++ Timeout +++++++++\n", 1, "TIMED_OUT"),
        (b"E   assert 'Timeout' in said\n1 failed\n", 1, "FAILED"),
        (b"1 failed\n", 1, "FAILED"),
        (b"error while collecting\n", 2, "FAILED"),
        (b"1 passed\n", 0, "PASSED"),
    ],
)
def test_the_line_pytest_writes_for_a_test_out_of_time_makes_the_run_one_out_of_time(
    mutate: Any, monkeypatch: Any, tmp_path: Path, said: bytes, code: int, verdict: str
) -> None:
    commands: list[list[str]] = []

    class Done:
        pid = 0

        async def wait(self) -> int:
            return code

    async def start(*command: str, **options: Any) -> Done:
        commands.append(list(command))
        options["stdout"].write(said)
        return Done()

    monkeypatch.setattr(mutate.asyncio, "create_subprocess_exec", start)
    runner = mutate.PytestRunner(["tests/a.py"], [])

    assert asyncio.run(runner(tmp_path, 20.0)) is TestVerdict[verdict]
    assert f"--timeout={mutate.TEST_TIMEOUT}" in commands[0]
    assert "--timeout-method=thread" in commands[0]


def test_a_mutant_that_never_ends_is_out_of_time_on_a_real_project(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    """``count`` walks up to ``n``. With the step of 1 turned into 0 it never gets there: the test
    goes past the limit of one test, the mutant is judged again alone, and the unchanged copy
    still passes that test in time, so the mutant stays out of time."""
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    source = "def count(n):\n    i = 0\n    while i < n:\n        i += 1\n    return i\n"
    (root / "src" / "walk.py").write_text(source, encoding="utf-8")
    (root / "tests" / "test_walk.py").write_text(
        "from walk import count\n\n\ndef test_count():\n    assert count(3) == 3\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mutate, "ROOT", root)
    monkeypatch.setattr(mutate, "deselected", lambda: [])
    monkeypatch.setattr(mutate, "TEST_TIMEOUT", 2)
    tests = ["tests/test_walk.py"]
    mutants = [m for m in mutate.mutants_of("src/walk.py", root, frozenset({4})) if m.line == 4]
    assert [m.change for m in mutants] == ["1 -> 0"]

    report, twice = mutate.judge_file("src/walk.py", mutants, tests, {}, 2)

    assert [m.status for m in report.mutants] == [MutantStatus.TIMEOUT]
    assert twice == []


# -- the verdict is the one every test of the file gives -------------------------------------------

TESTS = ["tests/a.py", "tests/b.py"]


def mutant(line: int, text: bytes, *, covered: bool = True) -> Mutant:
    return Mutant("src/m.py", line, f"change {line}", "f", text, covered)


def handed(mutate: Any, monkeypatch: Any, mutants: list[Mutant], timeout: float | None) -> Any:
    """What ``judge_file`` hands the executor for ``mutants``, and what it answers with."""
    calls: list[tuple[list[Mutant], Any, int, float | None]] = []
    answer = (MutationReport(()), [1])

    def judge(some: list[Mutant], runner: Any, workers: int, limit: float | None) -> Any:
        calls.append((some, runner, workers, limit))
        return answer

    monkeypatch.setattr(mutate, "judge", judge)
    monkeypatch.setattr(mutate, "deselected", lambda: ["--deselect", "x"])
    by_line = {"src/m.py": {1: {"tests/a.py"}, 2: {"tests/a.py"}, 3: {"tests/a.py", "tests/b.py"}}}
    assert mutate.judge_file("src/m.py", mutants, TESTS, by_line, 2, timeout) is answer
    ((some, runner, workers, limit),) = calls
    return some, runner, workers, limit


def test_a_mutant_is_first_judged_by_the_tests_of_its_line_and_then_by_every_test_of_the_file(
    mutate: Any, monkeypatch: Any
) -> None:
    """Coverage may name ``tests/a.py`` for a line whose change only ``tests/b.py`` notices: the
    executor judges by the whole runner whoever survives the first one."""
    mutants = [mutant(1, b"one"), mutant(2, b"two")]

    some, whole, workers, limit = handed(mutate, monkeypatch, mutants, 40.0)

    assert [(m.line, m.mutated, m.first.tests) for m in some] == [
        (1, b"one", ["tests/a.py"]),
        (2, b"two", ["tests/a.py"]),
    ]
    assert (whole.tests, whole.deselect, whole.processes) == (TESTS, ["--deselect", "x"], 1)
    assert (workers, limit) == (2, 40.0)
    # the same tests, the same runner: without a limit, the executor runs them once on the
    # unchanged copy and not once for each mutant
    assert some[0].first is some[1].first
    assert (some[0].first.deselect, some[0].first.processes) == (["--deselect", "x"], 1)


def test_a_mutant_whose_line_every_test_of_the_file_runs_is_judged_once_by_all_of_them(
    mutate: Any, monkeypatch: Any
) -> None:
    mutants = [mutant(3, b"three"), mutant(8, b"eight")]  # line 8: no test named, so all of them

    some, _, _, limit = handed(mutate, monkeypatch, mutants, None)

    assert [m.first for m in some] == [None, None]
    assert limit is None


def test_a_mutant_on_a_line_no_test_runs_keeps_its_place_and_is_still_not_run(
    mutate: Any, monkeypatch: Any
) -> None:
    mutants = [mutant(1, b"one", covered=False), mutant(2, b"two")]

    some, _, _, _ = handed(mutate, monkeypatch, mutants, 40.0)

    assert [(m.line, m.covered) for m in some] == [(1, False), (2, True)]


def test_the_copies_of_a_run_are_gone_when_it_ends_and_the_limit_reaches_the_executor(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    seen: list[tuple[Path, Path, object, object]] = []
    answer = (MutationReport(()), [0])

    async def run_mutants(
        worktree: Path, scratch: Path, mutants: Any, runner: Any, **how: Any
    ) -> Any:
        assert scratch.is_dir()
        seen.append((worktree, scratch, how["workers"], how["timeout"]))
        return answer

    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    monkeypatch.setattr(mutate, "run_mutants", run_mutants)
    runner = mutate.PytestRunner(TESTS, [])

    assert mutate.judge([], runner, 3, 40.0) is answer
    assert mutate.judge([], runner, 3) is answer
    assert [(worktree, workers, limit) for worktree, _, workers, limit in seen] == [
        (tmp_path, 3, 40.0),
        (tmp_path, 3, None),
    ]
    assert not any(scratch.exists() for _, scratch, _, _ in seen)


def test_a_mutant_judged_twice_was_judged_by_every_test_and_the_others_by_those_of_their_line(
    mutate: Any,
) -> None:
    mutants = [mutant(1, b"one"), mutant(3, b"three"), mutant(2, b"two"), mutant(8, b"eight")]
    by_line = {"src/m.py": {1: {"tests/a.py"}, 2: {"tests/a.py"}, 3: {"tests/b.py"}}}

    once = mutate.judges_of("src/m.py", mutants, TESTS, by_line, [])
    with_one_twice = mutate.judges_of("src/m.py", mutants, TESTS, by_line, [2])

    assert once == [["tests/a.py"], ["tests/b.py"], ["tests/a.py"], TESTS]  # line 8: no test named
    assert with_one_twice == [["tests/a.py"], ["tests/b.py"], TESTS, TESTS]


# -- the real thing, on a project of two files -----------------------------------------------------


def test_the_narrow_run_and_the_whole_run_agree_on_a_real_project(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    """``add`` is checked by one test file and ``sub`` only run by another, which checks nothing:
    the mutant of ``add`` is killed by its own tests alone, and the one of ``sub`` survives the
    tests of its line, is judged again by both files and survives them too."""
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    source = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
    (root / "src" / "calc.py").write_text(source, encoding="utf-8")
    (root / "tests" / "test_add.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
    )
    (root / "tests" / "test_sub.py").write_text(
        "from calc import sub\n\n\ndef test_sub():\n    sub(3, 1)\n", encoding="utf-8"
    )
    monkeypatch.setattr(mutate, "ROOT", root)
    monkeypatch.setattr(mutate, "deselected", lambda: [])
    tests = ["tests/test_add.py", "tests/test_sub.py"]
    by_line = {"src/calc.py": {2: {"tests/test_add.py"}, 6: {"tests/test_sub.py"}}}
    mutants = mutate.mutants_of("src/calc.py", root, frozenset({2, 6}))
    assert {m.line for m in mutants} == {2, 6}

    report, twice = mutate.judge_file("src/calc.py", mutants, tests, by_line, 2)
    whole, _ = mutate.judge(mutants, mutate.PytestRunner(tests, []), 2)

    assert [m.status for m in report.mutants] == [m.status for m in whole.mutants]
    by_status = {m.line: m.status for m in report.mutants}
    assert by_status == {2: MutantStatus.KILLED, 6: MutantStatus.SURVIVED}
    assert twice == [place for place, m in enumerate(mutants) if m.line == 6]


# -- the differential run: what the last one killed in a function left alone is not run again ------

CALC = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"


def test_the_hash_of_a_function_follows_its_text_and_not_the_space_that_ends_a_line(
    mutate: Any,
) -> None:
    before = mutate.form_digests(CALC, "calc.py")
    spaced = mutate.form_digests(CALC.replace("a + b\n", "a + b   \n"), "calc.py")
    changed = mutate.form_digests(CALC.replace("a + b", "b + a"), "calc.py")
    moved = mutate.form_digests("\n\n" + CALC, "calc.py")

    assert set(before) == {"add", "sub"}
    assert before["add"] != before["sub"]
    assert spaced == before == moved
    assert changed["add"] != before["add"]
    assert changed["sub"] == before["sub"]


def test_functions_of_one_name_are_hashed_together_in_the_order_of_the_file(mutate: Any) -> None:
    one, other = "def f(a):\n    return a + 1\n", "def f(a):\n    return a - 1\n"

    both = mutate.form_digests(one + other, "m.py")

    assert set(both) == {"f"}
    assert both["f"] != mutate.form_digests(other + one, "m.py")["f"]
    assert both["f"] not in (
        mutate.form_digests(one, "m.py")["f"],
        mutate.form_digests(other, "m.py")["f"],
    )


def calc_mutants(mutate: Any, tmp_path: Path, covered: frozenset[int]) -> list[Mutant]:
    (tmp_path / "calc.py").write_text(CALC, encoding="utf-8")
    found: list[Mutant] = mutate.mutants_of("calc.py", tmp_path, covered)
    return found


def test_a_mutant_is_named_by_its_file_function_place_and_change(
    mutate: Any, tmp_path: Path
) -> None:
    ids = mutate.mutation_ids(CALC, "calc.py")
    mutants = calc_mutants(mutate, tmp_path, frozenset({2, 6}))

    plus, minus = CALC.index("+"), CALC.index("-")
    assert ids == [
        f'["calc.py","add",{plus},{plus + 1},"+","-"]',
        f'["calc.py","sub",{minus},{minus + 1},"-","+"]',
    ]
    assert [(m.function, m.change) for m in mutants] == [("add", "+ -> -"), ("sub", "- -> +")]


def test_only_a_mutant_killed_before_in_a_function_left_as_it_was_is_not_run_again(
    mutate: Any, tmp_path: Path
) -> None:
    mutants = calc_mutants(mutate, tmp_path, frozenset({2, 6}))
    ids, digests = mutate.mutation_ids(CALC, "calc.py"), mutate.form_digests(CALC, "calc.py")
    killed = dict.fromkeys(ids, "killed")
    judges = dict.fromkeys(ids, ("tests/t.py",))
    tests = {"tests/t.py": "as it was"}

    def kept(forms: dict[str, str], outcomes: dict[str, str], some: list[Mutant]) -> set[int]:
        history = mutate.History(forms, outcomes, judges, tests)
        found: set[int] = mutate.carried(some, ids, digests, history, tests)
        return found

    assert kept(digests, killed, mutants) == {0, 1}
    assert kept(digests, {ids[0]: "killed", ids[1]: "survived"}, mutants) == {0}
    assert kept(digests, {ids[1]: "killed"}, mutants) == {1}  # the first was never judged
    assert kept({**digests, "add": "another text"}, killed, mutants) == {1}
    assert kept({"sub": digests["sub"]}, killed, mutants) == {1}  # add is new
    assert kept({}, killed, mutants) == set()
    assert kept(digests, {}, mutants) == set()
    # a line no test runs any more: the old verdict says nothing about it
    assert kept(digests, killed, calc_mutants(mutate, tmp_path, frozenset({6}))) == {1}


def test_a_mutant_whose_tests_are_not_what_they_were_is_run_again(
    mutate: Any, tmp_path: Path
) -> None:
    mutants = calc_mutants(mutate, tmp_path, frozenset({2, 6}))
    ids, digests = mutate.mutation_ids(CALC, "calc.py"), mutate.form_digests(CALC, "calc.py")
    killed = dict.fromkeys(ids, "killed")
    then = {"tests/a.py": "a", "tests/b.py": "b"}
    only_a = {"tests/a.py": "a"}

    def kept(
        judges: dict[str, tuple[str, ...]], before: dict[str, str], now: dict[str, str]
    ) -> set[int]:
        history = mutate.History(digests, killed, judges, before)
        found: set[int] = mutate.carried(mutants, ids, digests, history, now)
        return found

    both = {ids[0]: ("tests/a.py",), ids[1]: ("tests/a.py", "tests/b.py")}
    assert kept(both, then, then) == {0, 1}
    assert kept(both, then, {**then, "tests/b.py": "weaker"}) == {0}
    assert kept(both, then, {**then, "tests/a.py": "weaker"}) == set()
    assert kept(both, then, only_a) == {0}  # b has no hash now
    assert kept(both, only_a, then) == {0}  # the hash b had was never left
    assert kept(both, only_a, only_a) == {0}  # no hash then and none now is not the same hash
    assert kept({ids[0]: ("tests/a.py",)}, then, then) == {0}  # what killed the other was not left
    assert kept({ids[0]: (), ids[1]: ("tests/b.py",)}, then, then) == {1}
    assert kept({}, then, then) == set()  # what a run from before the tests were kept left


def test_the_hash_of_a_test_file_follows_its_content_every_conftest_and_the_tests_left_out(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    left_out: list[str] = []
    monkeypatch.setattr(mutate, "ROOT", tmp_path)
    monkeypatch.setattr(mutate, "deselected", lambda: left_out)
    (tmp_path / "tests" / "deep").mkdir(parents=True)
    (tmp_path / "tests" / "other").mkdir()
    one, other = "tests/test_one.py", "tests/deep/test_other.py"
    tight = "def test_one():\n    assert 1 + 1 == 2\n"
    (tmp_path / one).write_text(tight, encoding="utf-8")
    (tmp_path / other).write_text("def test_other():\n    assert True\n", encoding="utf-8")

    before = mutate.test_digests([one, other, one])
    assert list(before) == [other, one]
    assert before[one] != before[other]
    assert mutate.test_digests(iter([one, other])) == before

    (tmp_path / one).write_text("def test_one():\n    1 + 1\n", encoding="utf-8")
    weaker = mutate.test_digests([one, other])
    assert weaker[one] != before[one]
    assert weaker[other] == before[other]

    (tmp_path / one).write_text("", encoding="utf-8")
    empty = mutate.test_digests([one])[one]
    (tmp_path / one).unlink()
    gone = mutate.test_digests([one, other])
    assert gone[one] not in (before[one], weaker[one], empty)
    assert gone[other] == before[other]
    (tmp_path / one).write_text(tight, encoding="utf-8")
    assert mutate.test_digests([one, other]) == before

    # a fixture file in a folder that is above neither of them
    conftest = tmp_path / "tests" / "other" / "conftest.py"
    conftest.write_text("", encoding="utf-8")
    with_fixtures = mutate.test_digests([one, other])
    assert with_fixtures[one] != before[one]
    assert with_fixtures[other] != before[other]
    conftest.write_text("import pytest\n", encoding="utf-8")
    other_fixtures = mutate.test_digests([one, other])
    assert other_fixtures[one] != with_fixtures[one]
    conftest.unlink()
    assert mutate.test_digests([one, other]) == before

    left_out.extend(["--deselect", "tests/test_one.py::test_one"])
    assert mutate.test_digests([one, other])[other] != before[other]


def result(mutant: Mutant, status: MutantStatus) -> MutantResult:
    return MutantResult(mutant.file, mutant.line, mutant.change, status, mutant.function)


def test_the_report_puts_the_carried_mutants_back_in_their_places_as_killed(
    mutate: Any, tmp_path: Path
) -> None:
    mutants = calc_mutants(mutate, tmp_path, frozenset({2, 6})) * 2
    ran = MutationReport(
        (result(mutants[1], MutantStatus.SURVIVED), result(mutants[2], MutantStatus.UNCOVERED))
    )

    whole = mutate.with_carried(mutants, {0, 3}, ran)

    assert [(m.function, m.status) for m in whole.mutants] == [
        ("add", MutantStatus.KILLED),
        ("sub", MutantStatus.SURVIVED),
        ("add", MutantStatus.UNCOVERED),
        ("sub", MutantStatus.KILLED),
    ]
    assert whole.mutants[0] == result(mutants[0], MutantStatus.KILLED)
    assert mutate.with_carried(mutants[1:3], set(), ran).mutants == ran.mutants


def test_what_a_run_leaves_is_what_the_next_one_reads(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setattr(mutate, "SNAPSHOTS", tmp_path / ".metrics" / "mutate")
    mutants = calc_mutants(mutate, tmp_path, frozenset({2, 6})) * 2
    ids = ["one", "two", "three", "four"]
    statuses = (
        MutantStatus.KILLED,
        MutantStatus.SURVIVED,
        MutantStatus.TIMEOUT,
        MutantStatus.UNCOVERED,
    )
    report = MutationReport(tuple(map(result, mutants, statuses)))
    judged_by = [["tests/b.py", "tests/a.py"], ["tests/a.py"], ["tests/c.py"], ["tests/d.py"]]
    hashes = {f"tests/{letter}.py": letter.upper() for letter in "abcde"}
    assert mutate.load_history("src/calc.py") == mutate.NO_HISTORY

    path = mutate.write_history(
        "src/calc.py", {"add": "x", "sub": "y"}, ids, report, judged_by, hashes
    )

    assert path == tmp_path / ".metrics" / "mutate" / "src" / "calc.py.json"
    assert path.read_text(encoding="utf-8").endswith("}\n")
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2
    # only of a killed mutant are the tests left, and only the hashes of those tests
    assert mutate.load_history("src/calc.py") == mutate.History(
        {"add": "x", "sub": "y"},
        {"one": "killed", "two": "survived", "three": "killed"},
        {"one": ("tests/a.py", "tests/b.py"), "three": ("tests/c.py",)},
        {"tests/a.py": "A", "tests/b.py": "B", "tests/c.py": "C"},
    )
    assert mutate.load_history("src/other.py") == mutate.NO_HISTORY


@pytest.mark.parametrize(
    "written",
    [
        "",
        "{",
        "[]",
        '{"forms": {}}',
        '{"outcomes": {}}',
        '{"forms": [], "outcomes": {}}',
        '{"forms": {}, "outcomes": []}',
    ],
)
def test_a_history_that_cannot_be_read_is_no_history(
    mutate: Any, monkeypatch: Any, tmp_path: Path, written: str
) -> None:
    monkeypatch.setattr(mutate, "SNAPSHOTS", tmp_path)
    (tmp_path / "calc.py.json").write_text(written, encoding="utf-8")

    assert mutate.load_history("calc.py") == mutate.NO_HISTORY
    assert tuple(mutate.NO_HISTORY) == ({}, {}, {}, {})


def test_a_history_keeps_only_the_hashes_and_verdicts_it_understands(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setattr(mutate, "SNAPSHOTS", tmp_path)
    (tmp_path / "calc.py.json").write_text(
        '{"forms": {"add": "x", "sub": 3}, "outcomes": {"a": "killed", "b": "uncovered", "c": 1,'
        ' "d": "survived"}, "judges": {"a": ["tests/t.py", "tests/u.py"], "b": "tests/t.py",'
        ' "c": ["tests/t.py", 3], "d": []}, "tests": {"tests/t.py": "h", "tests/u.py": 7}}',
        encoding="utf-8",
    )

    assert mutate.load_history("calc.py") == mutate.History(
        {"add": "x"},
        {"a": "killed", "d": "survived"},
        {"a": ("tests/t.py", "tests/u.py"), "d": ()},
        {"tests/t.py": "h"},
    )


@pytest.mark.parametrize(
    "tests_part",
    ["", ', "judges": [], "tests": "x"', ', "judges": null, "tests": ["tests/t.py"]'],
)
def test_a_history_without_tests_it_can_read_keeps_its_verdicts_and_no_tests(
    mutate: Any, monkeypatch: Any, tmp_path: Path, tests_part: str
) -> None:
    monkeypatch.setattr(mutate, "SNAPSHOTS", tmp_path)
    (tmp_path / "calc.py.json").write_text(
        '{"version": 1, "forms": {"add": "x"}, "outcomes": {"a": "killed"}' + tests_part + "}",
        encoding="utf-8",
    )

    assert mutate.load_history("calc.py") == mutate.History({"add": "x"}, {"a": "killed"}, {}, {})


def test_every_mutant_or_only_those_since_the_last_run_and_not_both(mutate: Any) -> None:
    assert not mutate.parse_args([]).mutate_all
    assert not mutate.parse_args([]).since_last_run
    assert mutate.parse_args(["--mutate-all"]).mutate_all
    assert mutate.parse_args(["--since-last-run"]).since_last_run
    with pytest.raises(SystemExit):
        mutate.parse_args(["--mutate-all", "--since-last-run"])


def run_main(
    mutate: Any,
    monkeypatch: Any,
    root: Path,
    verdict: MutantStatus,
    *options: str,
    tests: tuple[str, ...] = ("tests/t.py",),
    lines: dict[int, set[str]] | None = None,
    twice: str = "",
    unchanged: list[Any] | None = None,
) -> tuple[int, list[str]]:
    """``main`` over ``src/calc.py`` of ``root`` with every mutant given ``verdict``: the exit
    code, and the functions of the mutants that were handed to the tests. ``tests`` are the test
    files that run the source file, ``lines`` those that run each line, and the mutants of the
    function ``twice`` are said to have been judged a second time. ``unchanged`` takes the tests
    of each run on the unchanged copy, and then the limit each file was judged with."""
    handed: list[str] = []
    noted = unchanged if unchanged is not None else []

    def time_limit(of_base: list[str], workers: int) -> float:
        noted.append(of_base)
        return 77.0

    def judge_file(
        file: str,
        mutants: list[Mutant],
        tests: list[str],
        by_line: Any,
        workers: int,
        timeout: float | None,
    ) -> tuple[MutationReport, list[int]]:
        noted.append(timeout)
        handed.extend(str(m.function) for m in mutants)
        again = [place for place, m in enumerate(mutants) if m.function == twice]
        return MutationReport(tuple(result(m, verdict) for m in mutants)), again

    coverage = ({"src/calc.py": list(tests)}, {"src/calc.py": lines or {}})
    monkeypatch.setattr(mutate, "time_limit", time_limit)
    monkeypatch.setattr(mutate, "ROOT", root)
    monkeypatch.setattr(mutate, "SNAPSHOTS", root / ".metrics" / "mutate")
    monkeypatch.setattr(mutate, "canary", lambda: True)
    monkeypatch.setattr(mutate, "suppressions", lambda: [])
    monkeypatch.setattr(mutate, "deselected", lambda: [])
    monkeypatch.setattr(mutate, "record_coverage", lambda *_: coverage)
    monkeypatch.setattr(mutate, "covered_lines", lambda: {"src/calc.py": frozenset({2, 6})})
    monkeypatch.setattr(mutate, "judge_file", judge_file)
    monkeypatch.setattr(sys, "argv", ["mutate.py", *options, "src/calc.py"])
    return mutate.main(), handed


def test_the_second_run_judges_only_the_survivors_and_the_function_that_changed(
    mutate: Any, monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "calc.py"
    source.write_text(CALC, encoding="utf-8")

    assert run_main(mutate, monkeypatch, tmp_path, MutantStatus.KILLED) == (0, ["add", "sub"])
    assert run_main(mutate, monkeypatch, tmp_path, MutantStatus.KILLED) == (0, [])
    said = capsys.readouterr().out
    assert "2 killed before, the function and its tests left as they were" in said

    source.write_text(CALC.replace("a - b", "b - a"), encoding="utf-8")
    assert run_main(mutate, monkeypatch, tmp_path, MutantStatus.SURVIVED) == (3, ["sub"])
    # a survivor is judged again, and is still one in what is left for the next run
    assert run_main(mutate, monkeypatch, tmp_path, MutantStatus.SURVIVED) == (3, ["sub"])
    every = run_main(mutate, monkeypatch, tmp_path, MutantStatus.KILLED, "--mutate-all")
    assert every == (0, ["add", "sub"])
    assert run_main(mutate, monkeypatch, tmp_path, MutantStatus.KILLED) == (0, [])


KILLED = MutantStatus.KILLED
CHECKS = "from calc import add\n\n\ndef test_it():\n    assert add(1, 2) == 3\n"
LOOKS_AWAY = "from calc import add\n\n\ndef test_it():\n    add(1, 2)\n"


def calc_project(root: Path, *tests: str) -> None:
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "calc.py").write_text(CALC, encoding="utf-8")
    for name in tests:
        (root / name).write_text(CHECKS, encoding="utf-8")


def test_a_mutant_whose_test_file_was_weakened_or_removed_is_judged_again(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    calc_project(tmp_path, "tests/t.py")
    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, ["add", "sub"])
    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, [])

    (tmp_path / "tests" / "t.py").write_text(LOOKS_AWAY, encoding="utf-8")
    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, ["add", "sub"])
    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, [])

    (tmp_path / "tests" / "t.py").unlink()
    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, ["add", "sub"])


def test_a_fixture_file_that_changed_anywhere_under_tests_has_every_mutant_judged_again(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    calc_project(tmp_path, "tests/t.py")
    (tmp_path / "tests" / "elsewhere").mkdir()
    conftest = tmp_path / "tests" / "elsewhere" / "conftest.py"
    conftest.write_text("", encoding="utf-8")
    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, ["add", "sub"])
    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, [])

    conftest.write_text("import pytest\n", encoding="utf-8")

    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, ["add", "sub"])
    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, [])


def test_a_mutant_killed_only_by_every_test_is_judged_again_when_any_of_them_changes(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    """Coverage names ``tests/a.py`` for both lines. The mutant of ``sub`` got through it and
    was killed the second time, by every test of the file: ``tests/b.py`` is among what killed
    it, and is not among what killed the mutant of ``add``."""
    calc_project(tmp_path, "tests/a.py", "tests/b.py")

    def run() -> tuple[int, list[str]]:
        return run_main(
            mutate,
            monkeypatch,
            tmp_path,
            KILLED,
            tests=("tests/a.py", "tests/b.py"),
            lines={2: {"tests/a.py"}, 6: {"tests/a.py"}},
            twice="sub",
        )

    assert run() == (0, ["add", "sub"])
    assert run() == (0, [])  # and what killed each is left again for the run after this one

    (tmp_path / "tests" / "b.py").write_text(LOOKS_AWAY, encoding="utf-8")
    assert run() == (0, ["sub"])
    assert run() == (0, [])

    (tmp_path / "tests" / "a.py").write_text(LOOKS_AWAY, encoding="utf-8")
    assert run() == (0, ["add", "sub"])


def test_what_a_run_from_before_the_tests_were_kept_left_carries_no_verdict(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    calc_project(tmp_path, "tests/t.py")
    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, ["add", "sub"])
    path = mutate.snapshot_path("src/calc.py")
    left = json.loads(path.read_text(encoding="utf-8"))
    old = {key: left[key] for key in ("source", "forms", "outcomes")} | {"version": 1}
    path.write_text(json.dumps(old), encoding="utf-8")

    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, ["add", "sub"])
    assert run_main(mutate, monkeypatch, tmp_path, KILLED) == (0, [])


def test_tests_that_fail_before_any_mutation_leave_the_history_as_it_was(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "calc.py"
    source.write_text(CALC, encoding="utf-8")
    assert run_main(mutate, monkeypatch, tmp_path, MutantStatus.KILLED)[0] == 0
    left = mutate.snapshot_path("src/calc.py").read_text(encoding="utf-8")
    source.write_text(CALC.replace("a - b", "b - a"), encoding="utf-8")

    def failing(*_: object) -> None:
        raise mutate.BaselineFailed("red")

    monkeypatch.setattr(mutate, "judge_file", failing)

    assert mutate.main() == 2
    assert mutate.snapshot_path("src/calc.py").read_text(encoding="utf-8") == left


def test_the_unchanged_copy_is_run_once_before_the_mutants_and_not_at_all_with_none_to_run(
    mutate: Any, monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    calc_project(tmp_path, "tests/t.py", "tests/a.py")
    tests = ("tests/t.py", "tests/a.py")
    unchanged: list[Any] = []

    def run() -> int:
        return run_main(mutate, monkeypatch, tmp_path, KILLED, tests=tests, unchanged=unchanged)[0]

    assert run() == 0
    # every test file of the source file, then the file judged with the limit that run gave
    assert unchanged == [["tests/a.py", "tests/t.py"], 77.0]
    assert "the unchanged copy: 2 test file(s)" in capsys.readouterr().out

    del unchanged[:]
    assert run() == 0
    assert unchanged == [None]  # the last run settled every mutant: no test runs, no limit
    assert "the unchanged copy" not in capsys.readouterr().out


def test_tests_that_fail_on_the_unchanged_copy_end_the_run_before_any_mutant(
    mutate: Any, monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    calc_project(tmp_path, "tests/t.py")
    assert run_main(mutate, monkeypatch, tmp_path, KILLED)[0] == 0
    left = mutate.snapshot_path("src/calc.py").read_text(encoding="utf-8")
    (tmp_path / "src" / "calc.py").write_text(CALC.replace("a - b", "b - a"), encoding="utf-8")
    judged: list[object] = []

    def red(*_: object) -> float:
        raise mutate.BaselineFailed("red")

    monkeypatch.setattr(mutate, "time_limit", red)
    monkeypatch.setattr(mutate, "judge_file", lambda *asked: judged.append(asked))
    capsys.readouterr()

    assert mutate.main() == 2
    assert "the tests fail before any mutation (red)" in capsys.readouterr().out
    assert judged == []
    assert mutate.snapshot_path("src/calc.py").read_text(encoding="utf-8") == left

    def gone(*_: object) -> float:
        raise mutate.TestsDidNotRun("pytest exited with 4")

    monkeypatch.setattr(mutate, "time_limit", gone)
    assert mutate.main() == 2
    assert "the tests fail before any mutation (pytest exited with 4)" in capsys.readouterr().out


def test_a_machine_too_busy_to_judge_a_mutant_ends_the_run_and_leaves_the_history_as_it_was(
    mutate: Any, monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "calc.py"
    source.write_text(CALC, encoding="utf-8")
    assert run_main(mutate, monkeypatch, tmp_path, MutantStatus.KILLED)[0] == 0
    left = mutate.snapshot_path("src/calc.py").read_text(encoding="utf-8")
    source.write_text(CALC.replace("a - b", "b - a"), encoding="utf-8")

    def busy(*_: object) -> None:
        raise mutate.MachineTooBusy("the machine is too busy")

    monkeypatch.setattr(mutate, "judge_file", busy)
    capsys.readouterr()

    assert mutate.main() == 2
    said = capsys.readouterr().out
    assert "src/calc.py: no verdict (the machine is too busy)" in said
    assert "the tests fail" not in said
    assert mutate.snapshot_path("src/calc.py").read_text(encoding="utf-8") == left
