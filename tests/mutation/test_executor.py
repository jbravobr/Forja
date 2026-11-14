import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from forja.core.reports.models import MutantStatus
from forja.mutation.executor import (
    BaselineFailed,
    Mutant,
    TestVerdict,
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


def run(project: Path, tmp_path: Path, mutants: list[Mutant], runner=pytest_runner, **kwargs):  # type: ignore[no-untyped-def]
    return asyncio.run(run_mutants(project, tmp_path / "scratch", mutants, runner, **kwargs))


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


def test_without_a_count_there_is_a_worker_per_processor_up_to_four(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 3)

    assert len(_roots_used(project, tmp_path)) == 3


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
