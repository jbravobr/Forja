"""The mutation gate's own script: which tests judge a mutant, and that the verdict stays the one
every test of the file would give."""

import asyncio
import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from forja.core.reports.models import MutantResult, MutantStatus, MutationReport
from forja.mutation.executor import Mutant

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


# -- the command line ------------------------------------------------------------------------------


def test_the_ceiling_of_runs_at_once_is_asked_for_by_the_name_the_mutator_gives_it(
    mutate: Any, monkeypatch: Any
) -> None:
    assert mutate.parse_args(["--max-workers", "3", "a.py"]).max_workers == 3
    monkeypatch.setattr(mutate.os, "cpu_count", lambda: 32)
    assert mutate.parse_args([]).max_workers == 8
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
    files = {
        "src\\forja\\nowhere.py": {
            "contexts": {"3": ["tests/a.py", ""], "4": ["tests/a.py", "tests/b.py"], "9": [""]}
        }
    }

    by_line = mutate.tests_by_line(files)

    assert by_line == {
        "src/forja/nowhere.py": {3: {"tests/a.py"}, 4: {"tests/a.py", "tests/b.py"}, 9: set()}
    }


def test_the_other_lines_of_a_statement_take_the_tests_of_its_first(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    source = tmp_path / "m.py"
    source.write_text("x = f(\n    1,\n    2,\n)\ny = 3\n", encoding="utf-8")
    monkeypatch.setattr(mutate, "test_file", lambda context: context or None)

    by_line = mutate.tests_by_line({str(source): {"contexts": {"1": ["tests/a.py"], "5": []}}})

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


# -- the runner knows the mutant by the content of the file ----------------------------------------


def test_the_runner_gives_a_mutant_its_own_tests_and_the_unchanged_copy_all_of_them(
    mutate: Any, tmp_path: Path
) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "m.py"
    narrowed = {mutate.fingerprint(b"mutant one"): ["tests/a.py"]}
    runner = mutate.PytestRunner(["tests/a.py", "tests/b.py"], [], "src/m.py", narrowed)

    source.write_bytes(b"original")
    assert runner.chosen(tmp_path) == ["tests/a.py", "tests/b.py"]
    source.write_bytes(b"mutant one")
    assert runner.chosen(tmp_path) == ["tests/a.py"]
    source.write_bytes(b"mutant nobody narrowed")
    assert runner.chosen(tmp_path) == ["tests/a.py", "tests/b.py"]


def test_a_runner_without_a_source_always_runs_every_test(mutate: Any, tmp_path: Path) -> None:
    runner = mutate.PytestRunner(["tests/a.py", "tests/b.py"], [])

    assert runner.chosen(tmp_path) == ["tests/a.py", "tests/b.py"]


# -- the unchanged copy is run over several processes, a mutant in one -----------------------------


def test_a_mutant_is_given_at_least_ten_times_what_one_process_would_take(mutate: Any) -> None:
    runner = mutate.PytestRunner(["tests/a.py"], [], base_workers=4)
    assert runner.patience(50.0) == 50.0  # nothing is known of the unchanged copy yet
    assert runner.patience(None) is None

    runner.base_seconds = 5.0  # over four processes: one alone takes up to twenty

    assert runner.patience(50.0) == 200.0
    assert runner.patience(500.0) == 500.0  # never less than the executor gives
    assert runner.patience(None) is None


def test_with_one_process_for_the_unchanged_copy_the_limit_is_the_executors(mutate: Any) -> None:
    runner = mutate.PytestRunner(["tests/a.py"], [])
    runner.base_seconds = 5.0

    assert runner.patience(50.0) == 50.0
    assert mutate.PytestRunner(["tests/a.py"], [], base_workers=0).base_workers == 1


def test_the_unchanged_copy_is_timed_and_run_over_the_processes_asked_for(
    mutate: Any, monkeypatch: Any, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    class Done:
        pid = 0

        async def wait(self) -> int:
            return 0

    async def start(*command: str, **options: object) -> Done:
        commands.append(list(command))
        return Done()

    monkeypatch.setattr(mutate.asyncio, "create_subprocess_exec", start)
    runner = mutate.PytestRunner(["tests/a.py"], [], base_workers=3)

    async def both() -> None:
        await runner(tmp_path, None)  # the unchanged copy: no limit
        await runner(tmp_path, 20.0)  # a mutant

    asyncio.run(both())

    assert "-n3" in commands[0] and "-n0" not in commands[0]
    assert "-n0" in commands[1] and "-n3" not in commands[1]
    assert runner.base_seconds is not None and runner.base_seconds >= 0


# -- the verdict is the one every test of the file gives -------------------------------------------

TESTS = ["tests/a.py", "tests/b.py"]


def mutant(line: int, text: bytes, *, covered: bool = True) -> Mutant:
    return Mutant("src/m.py", line, f"change {line}", "f", text, covered)


def judged(mutate: Any, monkeypatch: Any, mutants: list[Mutant], verdicts: Any) -> Any:
    """``judge_file`` with a ``judge`` that records each call and answers from ``verdicts``:
    ``verdicts(mutant, tests)`` is the status the tests given to that mutant would find."""
    calls: list[list[tuple[int, tuple[str, ...]]]] = []

    def judge(some: list[Mutant], runner: Any, workers: int) -> MutationReport:
        seen, results = [], []
        for one in some:
            tests = (
                tuple(runner.narrowed.get(mutate.fingerprint(one.mutated), runner.tests))
                if runner.source
                else tuple(runner.tests)
            )
            status = MutantStatus.UNCOVERED if not one.covered else verdicts(one, tests)
            seen.append((one.line, tests))
            results.append(MutantResult(one.file, one.line, one.change, status, one.function))
        calls.append(seen)
        return MutationReport(tuple(results))

    monkeypatch.setattr(mutate, "judge", judge)
    monkeypatch.setattr(mutate, "deselected", lambda: [])
    by_line = {"src/m.py": {1: {"tests/a.py"}, 2: {"tests/a.py"}, 3: {"tests/a.py", "tests/b.py"}}}
    report, twice = mutate.judge_file("src/m.py", mutants, TESTS, by_line, 2)
    return report, twice, calls


def test_a_mutant_the_tests_of_its_line_kill_is_killed_and_judged_once(
    mutate: Any, monkeypatch: Any
) -> None:
    mutants = [mutant(1, b"one"), mutant(2, b"two")]

    report, twice, calls = judged(
        mutate, monkeypatch, mutants, lambda one, tests: MutantStatus.KILLED
    )

    assert [m.status for m in report.mutants] == [MutantStatus.KILLED, MutantStatus.KILLED]
    assert twice == 0
    assert calls == [[(1, ("tests/a.py",)), (2, ("tests/a.py",))]]


def test_a_mutant_that_survives_the_tests_of_its_line_is_judged_again_by_every_test(
    mutate: Any, monkeypatch: Any
) -> None:
    """Coverage named ``tests/a.py`` for line 2, and it is ``tests/b.py`` that notices the change
    there: the narrow run lets the mutant through, the whole one kills it, as it always did."""
    mutants = [mutant(1, b"one"), mutant(2, b"two")]

    def verdicts(one: Mutant, tests: tuple[str, ...]) -> MutantStatus:
        noticed = one.line == 1 or "tests/b.py" in tests
        return MutantStatus.KILLED if noticed else MutantStatus.SURVIVED

    report, twice, calls = judged(mutate, monkeypatch, mutants, verdicts)

    assert [m.status for m in report.mutants] == [MutantStatus.KILLED, MutantStatus.KILLED]
    assert twice == 1
    assert calls == [
        [(1, ("tests/a.py",)), (2, ("tests/a.py",))],
        [(2, ("tests/a.py", "tests/b.py"))],
    ]


def test_a_mutant_no_test_at_all_notices_survives_after_both_runs(
    mutate: Any, monkeypatch: Any
) -> None:
    mutants = [mutant(1, b"one"), mutant(2, b"two")]

    def verdicts(one: Mutant, tests: tuple[str, ...]) -> MutantStatus:
        return MutantStatus.KILLED if one.line == 1 else MutantStatus.SURVIVED

    report, twice, calls = judged(mutate, monkeypatch, mutants, verdicts)

    assert [m.status for m in report.mutants] == [MutantStatus.KILLED, MutantStatus.SURVIVED]
    assert (twice, len(calls)) == (1, 2)


def test_a_survivor_already_judged_by_every_test_is_not_judged_again(
    mutate: Any, monkeypatch: Any
) -> None:
    mutants = [mutant(3, b"three"), mutant(8, b"eight")]  # line 8: no test named, so all of them

    report, twice, calls = judged(
        mutate, monkeypatch, mutants, lambda one, tests: MutantStatus.SURVIVED
    )

    assert [m.status for m in report.mutants] == [MutantStatus.SURVIVED, MutantStatus.SURVIVED]
    assert (twice, len(calls)) == (0, 1)
    assert calls[0] == [(3, ("tests/a.py", "tests/b.py")), (8, ("tests/a.py", "tests/b.py"))]


def test_a_mutant_on_a_line_no_test_runs_is_not_given_tests_and_keeps_its_place(
    mutate: Any, monkeypatch: Any
) -> None:
    mutants = [mutant(1, b"one", covered=False), mutant(2, b"two")]

    report, twice, _ = judged(mutate, monkeypatch, mutants, lambda one, tests: MutantStatus.KILLED)

    assert [(m.line, m.status) for m in report.mutants] == [
        (1, MutantStatus.UNCOVERED),
        (2, MutantStatus.KILLED),
    ]
    assert twice == 0


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
    whole = mutate.judge(mutants, mutate.PytestRunner(tests, []), 2)

    assert [m.status for m in report.mutants] == [m.status for m in whole.mutants]
    by_status = {m.line: m.status for m in report.mutants}
    assert by_status == {2: MutantStatus.KILLED, 6: MutantStatus.SURVIVED}
    assert twice == sum(1 for m in mutants if m.line == 6)
