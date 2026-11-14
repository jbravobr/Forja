"""Run mutants: change one file at a time in a throwaway copy and see whether the tests notice.

Nothing here knows a language. The caller hands over each mutant as the bytes the file would
have, whether a test runs the changed line, and a function that runs the whole test command.
As in unclebob/mutator a mutant on an uncovered line is not run, a failing or timed-out run kills
the mutant, and the timeout is ten times the baseline, never below two seconds.
"""

import asyncio
import contextlib
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Protocol

from forja.core.dependencies import link_dependencies
from forja.core.reports.models import MutantResult, MutantStatus, MutationReport

# What a worker never needs, wherever it is: the history, the installed packages (linked back in
# by ``link_dependencies``) and what the tools remember between runs.
LEFT_OUT = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".gradle",
    }
)
# At the root only, as the mutator leaves them out of a worker: the reports and the build output
# a run writes there. A coverage report alone can weigh gigabytes, and no test reads it. ``dist``,
# ``build`` and ``out`` stay, unlike in the mutator: the test command the Forja is given may run
# what was built there.
ROOT_LEFT_OUT = frozenset({"target", "coverage", ".metrics", ".forja-coverage", ".forja-cover.out"})
MIN_TIMEOUT = 2.0
TIMEOUT_FACTOR = 10.0


class TestVerdict(StrEnum):
    __test__ = False  # not a test class, whatever its name says to pytest

    PASSED = "passed"
    FAILED = "failed"  # a test failed or the code no longer loads: the change was noticed
    TIMED_OUT = "timed_out"


class TestsDidNotRun(Exception):
    """The tool could not run at all (not found, stopped): no verdict about any mutant."""

    __test__ = False


class TestRunner(Protocol):
    __test__: ClassVar[bool] = False

    async def __call__(self, root: Path, timeout: float | None) -> TestVerdict: ...


@dataclass(frozen=True)
class Mutant:
    file: str  # repository-relative POSIX path
    line: int
    change: str  # the tool's text for the change, e.g. "<= -> <"
    function: str | None
    mutated: bytes  # the whole file as the mutant has it
    covered: bool  # a test runs the line; if not the mutant is not run


class BaselineFailed(Exception):
    """The tests do not pass on the unchanged copy, so a failure would prove nothing."""


def _fresh_copy(source: Path, target: Path) -> None:
    def left_out(directory: str, names: list[str]) -> set[str]:
        at_root = Path(directory) == source
        return {name for name in names if name in LEFT_OUT or (at_root and name in ROOT_LEFT_OUT)}

    shutil.copytree(source, target, ignore=left_out)
    link_dependencies(source, target)


def _drop_bytecode(path: Path) -> None:
    """Remove what Python compiled of ``path``, as the mutator does: a bytecode file of the same
    size and second as the mutant would run the original code. No other file changes, so no other
    bytecode is stale."""
    for compiled in (path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"):
        with contextlib.suppress(OSError):
            compiled.unlink()


async def _judge(
    runner: TestRunner, root: Path, path: Path, original: bytes, mutant: Mutant, timeout: float
) -> MutantStatus:
    if not mutant.covered:
        return MutantStatus.UNCOVERED
    try:
        path.write_bytes(mutant.mutated)
        _drop_bytecode(path)
        verdict = await runner(root, timeout)
    finally:
        path.write_bytes(original)
        _drop_bytecode(path)
    return {
        TestVerdict.PASSED: MutantStatus.SURVIVED,
        TestVerdict.FAILED: MutantStatus.KILLED,
        TestVerdict.TIMED_OUT: MutantStatus.TIMEOUT,
    }[verdict]


async def _work(
    queue: "asyncio.Queue[tuple[int, Mutant]]",
    results: dict[int, MutantStatus],
    runner: TestRunner,
    root: Path,
    timeout: float,
) -> None:
    while True:
        try:
            index, mutant = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        path = root / mutant.file
        results[index] = await _judge(runner, root, path, path.read_bytes(), mutant, timeout)


async def _alone(
    mutants: Sequence[Mutant],
    results: dict[int, MutantStatus],
    runner: TestRunner,
    root: Path,
    timeout: float,
) -> None:
    """Judge again, one at a time, the mutants that ran out of time beside the others.

    The timeout comes from a baseline that ran alone. With several test commands running at once
    a mutant the tests would have passed may only be slow, and counting it as killed would raise
    the score for no reason. Alone, it gets the conditions the baseline had."""
    for index, mutant in enumerate(mutants):
        if results[index] is MutantStatus.TIMEOUT:
            path = root / mutant.file
            results[index] = await _judge(runner, root, path, path.read_bytes(), mutant, timeout)


async def run_mutants(
    worktree: Path,
    scratch: Path,
    mutants: Sequence[Mutant],
    runner: TestRunner,
    *,
    workers: int | None = None,
    baseline_timeout: float | None = None,
) -> MutationReport:
    """Judge every mutant. ``scratch`` is an empty directory for the copies; the caller removes it.

    The baseline run (the whole test command on the unchanged copy) must pass; its duration sets
    the timeout of each mutant at ten times that, but never below two seconds. With more than
    one worker, a mutant that timed out is judged once more with nothing else running. As in the
    mutator there are never more workers than mutants to run, and with none to run nothing is
    copied.
    """
    to_run = sum(1 for mutant in mutants if mutant.covered)
    if to_run == 0:
        return MutationReport(
            tuple(
                MutantResult(m.file, m.line, m.change, MutantStatus.UNCOVERED, m.function)
                for m in mutants
            )
        )
    asked = max(1, workers) if workers is not None else min(4, os.cpu_count() or 1)
    count = min(asked, to_run)
    roots = [scratch / f"worker-{n}" for n in range(count)]
    for root in roots:
        await asyncio.to_thread(_fresh_copy, worktree, root)
    loop = asyncio.get_running_loop()
    started = loop.time()
    if await runner(roots[0], baseline_timeout) is not TestVerdict.PASSED:
        raise BaselineFailed("the tests do not pass on the unchanged copy")
    timeout = max(MIN_TIMEOUT, TIMEOUT_FACTOR * (loop.time() - started))
    queue: asyncio.Queue[tuple[int, Mutant]] = asyncio.Queue()
    for item in enumerate(mutants):
        queue.put_nowait(item)
    results: dict[int, MutantStatus] = {}
    await asyncio.gather(*(_work(queue, results, runner, root, timeout) for root in roots))
    if count > 1:
        await _alone(mutants, results, runner, roots[0], timeout)
    return MutationReport(
        tuple(
            MutantResult(m.file, m.line, m.change, results[i], m.function)
            for i, m in enumerate(mutants)
        )
    )
