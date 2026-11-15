import asyncio
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from forja.core.reports.models import MutantStatus
from forja.mutation import executor
from forja.mutation.executor import (
    BaselineFailed,
    MachineTooBusy,
    Mutant,
    TestsDidNotRun,
    TestVerdict,
    baseline,
    run_mutants,
)

CALC = b"def is_pos(x):\n    return x > 0\n\n\ndef spin(x):\n    return x + 1\n"
TESTS = (
    b"from calc import is_pos, spin\n\n\n"
    b"def test_pos():\n    assert is_pos(1)\n    assert not is_pos(-1)\n\n\n"
    b"def test_spin():\n    assert spin(1) == 2\n"
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_bytes(CALC)
    (root / "tests" / "test_calc.py").write_bytes(TESTS)
    (root / "conftest.py").write_bytes(b"")
    return root


async def pytest_runner(root: Path, timeout: float | None) -> TestVerdict:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        cwd=root,
        env={k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        code = await asyncio.wait_for(process.wait(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return TestVerdict.TIMED_OUT
    return TestVerdict.PASSED if code == 0 else TestVerdict.FAILED


def mutant(old: bytes, new: bytes, *, covered: bool = True) -> Mutant:
    return Mutant("calc.py", 2, "change", "is_pos", CALC.replace(old, new), covered)


def both(project: Path, tmp_path: Path, mutants: list[Mutant], runner=pytest_runner, **kwargs):  # type: ignore[no-untyped-def]
    """The report, and the places of the mutants judged twice."""
    return asyncio.run(run_mutants(project, tmp_path / "scratch", mutants, runner, **kwargs))


def run(project: Path, tmp_path: Path, mutants: list[Mutant], runner=pytest_runner, **kwargs):  # type: ignore[no-untyped-def]
    return both(project, tmp_path, mutants, runner, **kwargs)[0]


def test_a_change_the_tests_notice_is_killed(project: Path, tmp_path: Path) -> None:
    report = run(project, tmp_path, [mutant(b"x > 0", b"x < 0")])
    assert [m.status for m in report.mutants] == [MutantStatus.KILLED]


def test_a_change_no_test_notices_survives(project: Path, tmp_path: Path) -> None:
    report = run(project, tmp_path, [mutant(b"x > 0", b"x >= 0")])
    assert [m.status for m in report.mutants] == [MutantStatus.SURVIVED]


def test_a_mutant_on_an_uncovered_line_is_not_run(project: Path, tmp_path: Path) -> None:
    calls: list[float | None] = []

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        calls.append(timeout)
        return TestVerdict.PASSED

    report = run(project, tmp_path, [mutant(b"x > 0", b"x < 0", covered=False)], runner)
    assert [m.status for m in report.mutants] == [MutantStatus.UNCOVERED]
    assert calls == []


def test_a_mutant_that_never_ends_times_out(project: Path, tmp_path: Path) -> None:
    hang = mutant(b"return x + 1", b"while True:\n        pass")

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        if (root / "calc.py").read_bytes() == hang.mutated:
            return TestVerdict.TIMED_OUT
        return await pytest_runner(root, timeout)

    report = run(project, tmp_path, [hang], runner)
    assert [m.status for m in report.mutants] == [MutantStatus.TIMEOUT]


def test_the_worktree_and_every_copy_are_left_as_they_were(project: Path, tmp_path: Path) -> None:
    run(project, tmp_path, [mutant(b"x > 0", b"x < 0"), mutant(b"x > 0", b"x >= 0")], workers=2)
    assert (project / "calc.py").read_bytes() == CALC
    for copy in (tmp_path / "scratch").glob("worker-*"):
        assert (copy / "calc.py").read_bytes() == CALC


def test_a_runner_that_raises_still_restores_the_file(project: Path, tmp_path: Path) -> None:
    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        if (root / "calc.py").read_bytes() != CALC:
            raise RuntimeError("tool gone")
        return TestVerdict.PASSED

    with pytest.raises(RuntimeError):
        run(project, tmp_path, [mutant(b"x > 0", b"x < 0")], runner, workers=1)
    assert (tmp_path / "scratch" / "worker-0" / "calc.py").read_bytes() == CALC


def test_failing_tests_on_the_unchanged_copy_stop_the_run(project: Path, tmp_path: Path) -> None:
    (project / "tests" / "test_calc.py").write_bytes(TESTS + b"\n\ndef test_bad():\n    assert 0\n")
    with pytest.raises(BaselineFailed):
        run(project, tmp_path, [mutant(b"x > 0", b"x < 0")])


def test_stale_bytecode_does_not_hide_a_change_of_the_same_size(
    project: Path, tmp_path: Path
) -> None:
    # `>` and `<` have the same size. Pinning the file time makes the compiled code of the
    # original look current, as it does when two mutants are written within the same second.
    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        os.utime(root / "calc.py", (1_700_000_000, 1_700_000_000))
        return await pytest_runner(root, timeout)

    killer = mutant(b"x > 0", b"x < 0")
    assert len(killer.mutated) == len(CALC)
    report = run(project, tmp_path, [killer], runner, workers=1)
    assert [m.status for m in report.mutants] == [MutantStatus.KILLED]


def test_results_keep_the_order_and_text_of_the_input(project: Path, tmp_path: Path) -> None:
    first, second = mutant(b"x > 0", b"x < 0"), mutant(b"x > 0", b"x >= 0")
    report = run(project, tmp_path, [first, second], workers=2)
    assert [(m.file, m.line, m.change, m.function) for m in report.mutants] == [
        ("calc.py", 2, "change", "is_pos")
    ] * 2
    assert [m.status for m in report.mutants] == [MutantStatus.KILLED, MutantStatus.SURVIVED]


def test_the_timeout_is_ten_times_the_baseline_and_never_below_two_seconds(
    project: Path, tmp_path: Path
) -> None:
    seen: list[float | None] = []

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        seen.append(timeout)
        return TestVerdict.FAILED if (root / "calc.py").read_bytes() != CALC else TestVerdict.PASSED

    run(project, tmp_path, [mutant(b"x > 0", b"x < 0")], runner, workers=1, baseline_timeout=600.0)
    assert seen[0] == 600.0
    assert seen[1] is not None and 2.0 <= seen[1] < 3.0


def test_a_copy_sees_the_installed_packages_and_leaves_them_in_place(
    project: Path, tmp_path: Path
) -> None:
    packages = project / "node_modules" / "dep"
    packages.mkdir(parents=True)
    (packages / "index.js").write_text("x", encoding="utf-8")
    seen: list[bool] = []

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        seen.append((root / "node_modules" / "dep" / "index.js").is_file())
        return TestVerdict.PASSED

    run(project, tmp_path, [mutant(b"x > 0", b"x < 0")], runner)

    assert seen and all(seen)
    assert (packages / "index.js").read_text(encoding="utf-8") == "x"  # the cleanup only unlinks


def _roots_used(project: Path, tmp_path: Path, **kwargs: object) -> set[Path]:
    seen: set[Path] = set()

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        seen.add(root)
        await asyncio.sleep(0.05)
        return TestVerdict.PASSED

    mutants = [mutant(b"x > 0", b"x < 0") for _ in range(8)]
    run(project, tmp_path, mutants, runner, **kwargs)
    return seen


def test_a_worker_count_of_zero_still_runs_one_worker(project: Path, tmp_path: Path) -> None:
    assert len(_roots_used(project, tmp_path, workers=0)) == 1


def test_without_a_count_there_is_a_worker_per_processor_as_in_the_mutator(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 6)

    assert len(_roots_used(project, tmp_path)) == 6


def test_the_copies_of_the_workers_are_made_at_the_same_time(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    together = threading.Barrier(3, timeout=10)
    copy = executor._fresh_copy  # noqa: SLF001

    def waiting(source: Path, target: Path) -> None:
        together.wait()  # raises unless the three copies are under way at once
        copy(source, target)

    monkeypatch.setattr(executor, "_fresh_copy", waiting)

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        return TestVerdict.PASSED

    mutants = [mutant(b"x > 0", b"x < 0") for _ in range(3)]
    report = run(project, tmp_path, mutants, runner, workers=3)

    assert [m.status for m in report.mutants] == [MutantStatus.SURVIVED] * 3
    assert sorted(path.name for path in (tmp_path / "scratch").iterdir()) == [
        "worker-0",
        "worker-1",
        "worker-2",
    ]


def test_when_the_processors_cannot_be_counted_one_worker_runs(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: None)

    assert len(_roots_used(project, tmp_path)) == 1


def test_bytecode_that_cannot_be_removed_does_not_stop_the_run(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = Path.unlink
    refused: list[str] = []

    def locked(self: Path, missing_ok: bool = False) -> None:
        if self.suffix == ".pyc":
            refused.append(self.name)
            raise PermissionError("in use")
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)

    report = run(project, tmp_path, [mutant(b"x > 0", b"x < 0")], workers=1)

    assert refused and all(name.startswith("calc.") for name in refused)
    assert len(report.mutants) == 1


def test_only_the_bytecode_of_the_mutated_file_is_removed(project: Path, tmp_path: Path) -> None:
    kept: list[bool] = []

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        cache = root / "__pycache__"
        if (root / "calc.py").read_bytes() == CALC:  # the unchanged copy: as if Python had run
            cache.mkdir(exist_ok=True)
            for name in (
                "calc.cpython-312.pyc",
                "calculus.cpython-312.pyc",
                "other.cpython-312.pyc",
            ):
                (cache / name).write_bytes(b"compiled")
            return TestVerdict.PASSED
        kept.append((cache / "calc.cpython-312.pyc").exists())
        kept.append((cache / "calculus.cpython-312.pyc").exists())
        kept.append((cache / "other.cpython-312.pyc").exists())
        (cache / "calc.cpython-312.pyc").write_bytes(b"of the mutant")
        return TestVerdict.FAILED

    run(project, tmp_path, [mutant(b"x > 0", b"x < 0")], runner, workers=1)

    assert kept == [False, True, True]
    # what the mutant compiled is removed too, or the next run would load it
    assert not (tmp_path / "scratch" / "worker-0" / "__pycache__" / "calc.cpython-312.pyc").exists()


def test_a_copy_leaves_out_the_reports_at_the_root_and_the_caches_everywhere(
    project: Path, tmp_path: Path
) -> None:
    for left in ("target/coverage/big.json", "coverage/lcov.info", ".metrics/crap.edn"):
        (project / left).parent.mkdir(parents=True, exist_ok=True)
        (project / left).write_text("x", encoding="utf-8")
    (project / ".forja-cover.out").write_text("x", encoding="utf-8")
    for cache in ("__pycache__/a.pyc", "pkg/.pytest_cache/v", "pkg/.mypy_cache/v", "pkg/.gradle/v"):
        (project / cache).parent.mkdir(parents=True, exist_ok=True)
        (project / cache).write_text("x", encoding="utf-8")
    for kept in ("pkg/target/classes/A.class", "pkg/coverage/mod.py", "dist/app.js", "build/x"):
        (project / kept).parent.mkdir(parents=True, exist_ok=True)
        (project / kept).write_text("x", encoding="utf-8")

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        return TestVerdict.PASSED

    run(project, tmp_path, [mutant(b"x > 0", b"x < 0")], runner, workers=1)

    copy = tmp_path / "scratch" / "worker-0"
    found = {path.relative_to(copy).as_posix() for path in copy.rglob("*") if path.is_file()}
    assert found == {
        "calc.py",
        "conftest.py",
        "tests/test_calc.py",
        "pkg/target/classes/A.class",
        "pkg/coverage/mod.py",
        "dist/app.js",
        "build/x",
    }


def test_there_are_never_more_copies_than_mutants_to_run(project: Path, tmp_path: Path) -> None:
    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        return TestVerdict.PASSED

    mutants = [
        mutant(b"x > 0", b"x < 0"),
        mutant(b"x > 0", b"x >= 0"),
        mutant(b"x > 0", b"x != 0", covered=False),
    ]
    run(project, tmp_path, mutants, runner, workers=8)

    assert sorted(p.name for p in (tmp_path / "scratch").iterdir()) == ["worker-0", "worker-1"]


def test_with_no_mutant_to_run_nothing_is_copied_and_no_test_runs(
    project: Path, tmp_path: Path
) -> None:
    calls: list[Path] = []

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        calls.append(root)
        return TestVerdict.PASSED

    first, second = mutant(b"x > 0", b"x < 0", covered=False), mutant(b"1", b"0", covered=False)
    report = run(project, tmp_path, [first, second], runner, workers=4)

    assert [m.status for m in report.mutants] == [MutantStatus.UNCOVERED] * 2
    assert [(m.file, m.line, m.change, m.function) for m in report.mutants] == [
        ("calc.py", 2, "change", "is_pos")
    ] * 2
    assert calls == []
    assert not (tmp_path / "scratch").exists()


SLOW = CALC.replace(b"x > 0", b"x >= 0")


def _slow_beside_the_others(answers: list[TestVerdict], calls: list[Path]) -> object:
    """A runner whose runs of the ``SLOW`` mutant answer ``answers`` in turn; the unchanged copy
    passes and any other mutant is killed."""

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        content = (root / "calc.py").read_bytes()
        if content == CALC:
            return TestVerdict.PASSED
        if content != SLOW:
            return TestVerdict.FAILED
        calls.append(root)
        return answers[len(calls) - 1]

    return runner


def test_a_mutant_that_only_ran_out_of_time_beside_the_others_is_judged_again_alone(
    project: Path, tmp_path: Path
) -> None:
    calls: list[Path] = []
    runner = _slow_beside_the_others([TestVerdict.TIMED_OUT, TestVerdict.PASSED], calls)
    mutants = [mutant(b"x > 0", b"x >= 0"), mutant(b"x > 0", b"x < 0")]

    report = run(project, tmp_path, mutants, runner, workers=2)

    assert [m.status for m in report.mutants] == [MutantStatus.SURVIVED, MutantStatus.KILLED]
    assert len(calls) == 2
    assert calls[1] == tmp_path / "scratch" / "worker-0"


def test_a_mutant_that_runs_out_of_time_alone_too_is_a_timeout(
    project: Path, tmp_path: Path
) -> None:
    calls: list[Path] = []
    runner = _slow_beside_the_others([TestVerdict.TIMED_OUT, TestVerdict.TIMED_OUT], calls)
    mutants = [mutant(b"x > 0", b"x >= 0"), mutant(b"x > 0", b"x < 0")]

    report = run(project, tmp_path, mutants, runner, workers=2)

    assert [m.status for m in report.mutants] == [MutantStatus.TIMEOUT, MutantStatus.KILLED]
    assert len(calls) == 2
    for copy in (tmp_path / "scratch").iterdir():
        assert (copy / "calc.py").read_bytes() == CALC


def test_a_single_mutant_ran_alone_whatever_the_workers_asked_and_is_not_judged_again(
    project: Path, tmp_path: Path
) -> None:
    calls: list[Path] = []
    runner = _slow_beside_the_others([TestVerdict.TIMED_OUT], calls)

    report = run(project, tmp_path, [mutant(b"x > 0", b"x >= 0")], runner, workers=2)

    assert [m.status for m in report.mutants] == [MutantStatus.TIMEOUT]
    assert len(calls) == 1


def test_with_one_worker_a_timeout_is_not_judged_again(project: Path, tmp_path: Path) -> None:
    calls: list[Path] = []
    runner = _slow_beside_the_others([TestVerdict.TIMED_OUT], calls)

    report = run(project, tmp_path, [mutant(b"x > 0", b"x >= 0")], runner, workers=1)

    assert [m.status for m in report.mutants] == [MutantStatus.TIMEOUT]
    assert len(calls) == 1


# -- a timeout only kills while the unchanged copy still passes in the same time -------------------


def _scripted(
    unchanged: list[TestVerdict], slow: list[TestVerdict], log: list[tuple[str, float | None]]
) -> object:
    """A runner that answers ``unchanged`` in turn for the unchanged copy and ``slow`` in turn for
    the ``SLOW`` mutant, kills any other mutant, and notes each run and the time it was given."""

    def turn(kind: str, timeout: float | None, answers: list[TestVerdict]) -> TestVerdict:
        log.append((kind, timeout))
        return answers[[name for name, _ in log].count(kind) - 1]

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        content = (root / "calc.py").read_bytes()
        if content == CALC:
            return turn("unchanged", timeout, unchanged)
        if content == SLOW:
            return turn("slow", timeout, slow)
        return turn("other", timeout, [TestVerdict.FAILED] * 9)

    return runner


def kinds(log: list[tuple[str, float | None]]) -> list[str]:
    return [kind for kind, _ in log]


def test_a_timeout_alone_stands_when_the_unchanged_copy_still_passes_in_the_same_time(
    project: Path, tmp_path: Path
) -> None:
    log: list[tuple[str, float | None]] = []
    passed = TestVerdict.PASSED
    runner = _scripted([passed, passed], [TestVerdict.TIMED_OUT], log)

    report = run(project, tmp_path, [mutant(b"x > 0", b"x >= 0")], runner, workers=1)

    assert [m.status for m in report.mutants] == [MutantStatus.TIMEOUT]
    assert kinds(log) == ["unchanged", "slow", "unchanged"]
    assert log[2][1] is not None and log[2][1] == log[1][1]  # the time the mutant was given


def test_a_timeout_on_a_machine_too_busy_for_the_unchanged_copy_gives_no_verdict(
    project: Path, tmp_path: Path
) -> None:
    log: list[tuple[str, float | None]] = []
    runner = _scripted([TestVerdict.PASSED, TestVerdict.TIMED_OUT], [TestVerdict.TIMED_OUT], log)
    mutants = [mutant(b"x > 0", b"x >= 0"), mutant(b"x > 0", b"x < 0")]

    with pytest.raises(MachineTooBusy, match="too busy"):
        run(project, tmp_path, mutants, runner, workers=1)

    # checked at once: the mutant after it never ran, and the copy was left as it was
    assert kinds(log) == ["unchanged", "slow", "unchanged"]
    assert (tmp_path / "scratch" / "worker-0" / "calc.py").read_bytes() == CALC
    assert issubclass(MachineTooBusy, TestsDidNotRun)  # the gate reads it as no measurement


def test_a_mutant_out_of_time_beside_the_others_and_alone_is_checked_against_the_unchanged_copy(
    project: Path, tmp_path: Path
) -> None:
    log: list[tuple[str, float | None]] = []
    timed_out = TestVerdict.TIMED_OUT
    runner = _scripted([TestVerdict.PASSED, timed_out], [timed_out, timed_out], log)
    mutants = [mutant(b"x > 0", b"x >= 0"), mutant(b"x > 0", b"x < 0")]

    with pytest.raises(MachineTooBusy):
        run(project, tmp_path, mutants, runner, workers=2)

    assert kinds(log).count("slow") == 2
    assert kinds(log)[-2:] == ["slow", "unchanged"]


def test_a_mutant_that_passes_alone_asks_nothing_more_of_the_unchanged_copy(
    project: Path, tmp_path: Path
) -> None:
    log: list[tuple[str, float | None]] = []
    runner = _scripted([TestVerdict.PASSED], [TestVerdict.TIMED_OUT, TestVerdict.PASSED], log)
    mutants = [mutant(b"x > 0", b"x >= 0"), mutant(b"x > 0", b"x < 0")]

    report = run(project, tmp_path, mutants, runner, workers=2)

    assert [m.status for m in report.mutants] == [MutantStatus.SURVIVED, MutantStatus.KILLED]
    assert kinds(log).count("unchanged") == 1


def test_beside_the_others_a_timeout_is_not_yet_checked_against_the_unchanged_copy(
    project: Path, tmp_path: Path
) -> None:
    log: list[tuple[str, float | None]] = []
    passed, timed_out = TestVerdict.PASSED, TestVerdict.TIMED_OUT
    runner = _scripted([passed, passed], [timed_out, timed_out], log)
    mutants = [mutant(b"x > 0", b"x >= 0"), mutant(b"x > 0", b"x < 0")]

    report = run(project, tmp_path, mutants, runner, workers=2)

    assert [m.status for m in report.mutants] == [MutantStatus.TIMEOUT, MutantStatus.KILLED]
    # once before any mutant and once after the second timeout, never after the first
    assert kinds(log).count("unchanged") == 2
    assert kinds(log)[-2:] == ["slow", "unchanged"]


def test_tests_that_stopped_passing_on_the_unchanged_copy_after_a_timeout_stop_the_run(
    project: Path, tmp_path: Path
) -> None:
    log: list[tuple[str, float | None]] = []
    runner = _scripted([TestVerdict.PASSED, TestVerdict.FAILED], [TestVerdict.TIMED_OUT], log)

    with pytest.raises(BaselineFailed, match="no longer pass"):
        run(project, tmp_path, [mutant(b"x > 0", b"x >= 0")], runner, workers=1)


# -- a smaller run judges a mutant first, and the whole command whoever survives it ----------------

KILLER = CALC.replace(b"x > 0", b"x < 0")
OTHER = CALC.replace(b"x > 0", b"x != 0")
Log = list[tuple[str, str, float | None]]


def _noting(
    name: str, log: Log, answers: dict[bytes, TestVerdict] | None = None, pause: float = 0.0
) -> object:
    """A runner that notes each of its runs (its name, what the copy held, the time given). The
    unchanged copy passes after ``pause`` seconds; a mutant gets its answer of ``answers``, and
    without one the tests pass."""

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        content = (root / "calc.py").read_bytes()
        log.append((name, "unchanged" if content == CALC else "mutant", timeout))
        if content == CALC:
            await asyncio.sleep(pause)
            return TestVerdict.PASSED
        return (answers or {}).get(content, TestVerdict.PASSED)

    return runner


def aimed(content: bytes, first: object) -> Mutant:
    return Mutant("calc.py", 2, "change", "is_pos", content, True, first)  # type: ignore[arg-type]


def runs(log: Log) -> list[tuple[str, str]]:
    return [(name, held) for name, held, _ in log]


def test_a_mutant_its_first_runner_kills_never_reaches_the_whole_command(
    project: Path, tmp_path: Path
) -> None:
    log: Log = []
    first = _noting("first", log, {KILLER: TestVerdict.FAILED})
    whole = _noting("whole", log)

    report, twice = both(project, tmp_path, [aimed(KILLER, first)], whole, workers=1)

    assert [m.status for m in report.mutants] == [MutantStatus.KILLED]
    assert twice == []
    # not even on the unchanged copy: the whole command has a baseline only if a mutant needs it
    assert runs(log) == [("first", "unchanged"), ("first", "mutant")]


def test_a_mutant_that_survives_its_first_runner_is_judged_by_the_whole_command(
    project: Path, tmp_path: Path
) -> None:
    log: Log = []
    first = _noting("first", log)  # notices nothing
    whole = _noting("whole", log, {KILLER: TestVerdict.FAILED, OTHER: TestVerdict.FAILED})
    mutants = [aimed(KILLER, first), aimed(SLOW, first), mutant(b"x > 0", b"x != 0")]

    report, twice = both(project, tmp_path, mutants, whole, workers=1)

    assert [m.status for m in report.mutants] == [
        MutantStatus.KILLED,
        MutantStatus.SURVIVED,
        MutantStatus.KILLED,
    ]
    assert twice == [0, 1]  # the third has no first runner: the whole command judged it once
    assert runs(log) == [
        ("first", "unchanged"),  # one baseline for the two mutants that share the runner
        ("whole", "unchanged"),
        ("first", "mutant"),
        ("first", "mutant"),
        ("whole", "mutant"),
        ("whole", "mutant"),
        ("whole", "mutant"),
    ]


def test_each_runner_gives_a_mutant_ten_times_its_own_baseline(
    project: Path, tmp_path: Path
) -> None:
    log: Log = []
    first = _noting("first", log, pause=0.4)  # ten times that is well over the floor of two
    whole = _noting("whole", log)

    both(project, tmp_path, [aimed(SLOW, first)], whole, workers=1, baseline_timeout=600.0)

    assert runs(log) == [
        ("first", "unchanged"),
        ("first", "mutant"),
        ("whole", "unchanged"),
        ("whole", "mutant"),
    ]
    given = [timeout for _, _, timeout in log]
    assert given[0] == given[2] == 600.0
    assert given[1] is not None and given[1] >= 3.5
    assert given[3] is not None and 2.0 <= given[3] < 3.0


def test_a_limit_the_caller_measured_is_the_limit_of_every_run_and_no_baseline_is_run(
    project: Path, tmp_path: Path
) -> None:
    log: Log = []
    first = _noting("first", log)
    whole = _noting("whole", log, {KILLER: TestVerdict.FAILED})
    mutants = [aimed(SLOW, first), mutant(b"x > 0", b"x < 0")]

    report, twice = both(project, tmp_path, mutants, whole, workers=1, timeout=7.5)

    assert [m.status for m in report.mutants] == [MutantStatus.SURVIVED, MutantStatus.KILLED]
    assert twice == [0]
    assert log == [("first", "mutant", 7.5), ("whole", "mutant", 7.5), ("whole", "mutant", 7.5)]


def test_a_mutant_out_of_time_in_its_first_runner_is_checked_by_it_against_the_unchanged_copy(
    project: Path, tmp_path: Path
) -> None:
    log: Log = []
    first = _noting("first", log, {SLOW: TestVerdict.TIMED_OUT})
    whole = _noting("whole", log)

    report, twice = both(project, tmp_path, [aimed(SLOW, first)], whole, workers=1)

    assert [m.status for m in report.mutants] == [MutantStatus.TIMEOUT]
    assert twice == []  # out of time is no survivor: the whole command has nothing to add
    assert runs(log) == [("first", "unchanged"), ("first", "mutant"), ("first", "unchanged")]
    assert log[2][2] is not None and log[2][2] == log[1][2]


def test_a_first_runner_that_does_not_pass_on_the_unchanged_copy_stops_the_run(
    project: Path, tmp_path: Path
) -> None:
    log: Log = []

    async def red(root: Path, timeout: float | None) -> TestVerdict:
        return TestVerdict.FAILED

    with pytest.raises(BaselineFailed):
        both(project, tmp_path, [aimed(KILLER, red)], _noting("whole", log), workers=1)

    assert log == []


def test_a_lone_survivor_of_the_first_runners_is_judged_alone_by_the_whole_command(
    project: Path, tmp_path: Path
) -> None:
    """Three copies judged three mutants; one survived and only it goes to the whole command, in
    one copy. Out of time there it ran alone, so it is not judged again: the unchanged copy is
    asked for at once."""
    log: Log = []
    first = _noting("first", log, {KILLER: TestVerdict.FAILED, OTHER: TestVerdict.FAILED})
    whole = _noting("whole", log, {SLOW: TestVerdict.TIMED_OUT})
    mutants = [aimed(KILLER, first), aimed(SLOW, first), aimed(OTHER, first)]

    report, twice = both(project, tmp_path, mutants, whole, workers=3)

    assert [m.status for m in report.mutants] == [
        MutantStatus.KILLED,
        MutantStatus.TIMEOUT,
        MutantStatus.KILLED,
    ]
    assert twice == [1]
    assert runs(log)[-3:] == [("whole", "unchanged"), ("whole", "mutant"), ("whole", "unchanged")]


def test_the_survivors_of_the_first_runners_are_judged_by_the_whole_command_side_by_side(
    project: Path, tmp_path: Path
) -> None:
    log: Log = []
    first = _noting("first", log)
    used: list[Path] = []
    together = asyncio.Barrier(2)

    async def whole(root: Path, timeout: float | None) -> TestVerdict:
        if (root / "calc.py").read_bytes() != CALC:
            used.append(root)
            await asyncio.wait_for(together.wait(), 10)  # raises unless two run at once
        return TestVerdict.PASSED

    mutants = [aimed(KILLER, first), aimed(SLOW, first)]

    report, twice = both(project, tmp_path, mutants, whole, workers=2)

    assert [m.status for m in report.mutants] == [MutantStatus.SURVIVED] * 2
    assert twice == [0, 1]
    assert sorted(root.name for root in used) == ["worker-0", "worker-1"]


# -- the baseline of a command, for a caller that measures it once ---------------------------------


def test_the_baseline_of_a_command_is_timed_on_an_unchanged_copy_made_for_it(
    project: Path, tmp_path: Path
) -> None:
    (project / "target").mkdir()
    (project / "target" / "report.json").write_text("x", encoding="utf-8")
    seen: list[tuple[Path, float | None, bytes, bool]] = []

    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        seen.append((root, timeout, (root / "calc.py").read_bytes(), (root / "target").exists()))
        await asyncio.sleep(0.2)
        return TestVerdict.PASSED

    seconds = asyncio.run(baseline(project, tmp_path / "base", runner, 30.0))  # type: ignore[arg-type]

    # the copy a worker gets: without the reports at the root
    assert seen == [(tmp_path / "base", 30.0, CALC, False)]
    assert 0.1 < seconds < 5.0


def test_a_command_that_does_not_pass_on_the_unchanged_copy_has_no_baseline(
    project: Path, tmp_path: Path
) -> None:
    async def runner(root: Path, timeout: float | None) -> TestVerdict:
        assert timeout is None
        return TestVerdict.TIMED_OUT

    with pytest.raises(BaselineFailed):
        asyncio.run(baseline(project, tmp_path / "base", runner))  # type: ignore[arg-type]
