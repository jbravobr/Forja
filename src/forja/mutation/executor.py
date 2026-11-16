"""Run mutants: change one file at a time in a throwaway copy and see whether the tests notice.

Nothing here knows a language. The caller hands over each mutant as the bytes the file would
have, whether a test runs the changed line, and a function that runs the whole test command.
As in unclebob/mutator a mutant on an uncovered line is not run, a failing or timed-out run kills
the mutant, and the timeout is ten times the baseline, never below two seconds, with one baseline
for each command that judges a mutant. Unlike the mutator, a timeout only kills while the unchanged
copy still passes within that same time: on a machine too busy for that the run ends without a
verdict (``MachineTooBusy``).

A mutant may name a smaller run that judges it first (``Mutant.first``): in Go the package of the
file, as in the mutator, or the tests that run its lines. Killed by it, the mutant is killed by
the whole command, which holds that run. One that survives it is not yet a survivor: the whole
command judges it before it counts. A smaller run judges nobody unless it passes on the unchanged
copy: tests chosen out of the whole command may fail by themselves, whatever the code is.
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
ROOT_LEFT_OUT = frozenset(
    {"target", "coverage", ".metrics", ".forja-coverage", ".forja-cover.out", ".forja-test-lines"}
)
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
    # a run smaller than the whole command, and held by it, that judges the mutant first. Mutants
    # whose runners are equal share the baseline
    first: TestRunner | None = None
    # what names the mutant from one run to the next (the id the mutator gives it), for a caller
    # that keeps a history
    id: str = ""
    # the lines whose coverage counts for it (its own, and the one its statement starts on), for
    # a caller that chooses the first run by the tests that run them
    lines: tuple[int, ...] = ()


class BaselineFailed(Exception):
    """The tests do not pass on the unchanged copy, so a failure would prove nothing."""


class MachineTooBusy(TestsDidNotRun):
    """The unchanged copy no longer passes within the time a mutant is given, so a mutant that
    ran out of time proves nothing: the machine is what is slow."""


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


async def _judge(runner: TestRunner, root: Path, mutant: Mutant, timeout: float) -> MutantStatus:
    path = root / mutant.file
    original = path.read_bytes()
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


async def _still_in_time(runner: TestRunner, root: Path, timeout: float) -> None:
    """A mutant ran out of time with nothing else running. That only kills it if the unchanged
    copy, run right after it, still passes within the same limit.

    The limit comes from a baseline that ran while the machine was free. The tests run at the
    lowest priority, so on a machine that has since become busy they hardly run at all: a mutant
    the tests would have passed runs out of time, and counting it as killed would approve what
    should be rejected. The mutator counts every timeout as a kill; here a timeout the machine
    explains ends the run without a verdict."""
    verdict = await runner(root, timeout)
    if verdict is TestVerdict.TIMED_OUT:
        raise MachineTooBusy(
            "the unchanged copy no longer passes in the time a mutant is given: the machine is "
            "too busy to tell a slow mutant from one that never ends"
        )
    if verdict is not TestVerdict.PASSED:
        raise BaselineFailed("the tests no longer pass on the unchanged copy")


# One run to make: the place of the mutant, the mutant, who judges it and in how long.
_Run = tuple[int, Mutant, TestRunner, float]


async def _work(
    queue: "asyncio.Queue[_Run]", results: dict[int, MutantStatus], root: Path, alone: bool
) -> None:
    """Make runs from the queue in the copy ``root``. ``alone`` says no other worker runs: a
    timeout is then final, and is checked against the unchanged copy at once."""
    while True:
        try:
            index, mutant, runner, timeout = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        results[index] = await _judge(runner, root, mutant, timeout)
        if alone and results[index] is MutantStatus.TIMEOUT:
            await _still_in_time(runner, root, timeout)


async def _alone(runs: Sequence[_Run], results: dict[int, MutantStatus], root: Path) -> None:
    """Judge again, one at a time, the mutants that ran out of time beside the others.

    The timeout comes from a baseline that ran alone. With several test commands running at once
    a mutant the tests would have passed may only be slow, and counting it as killed would raise
    the score for no reason. Alone, it gets the conditions the baseline had; one that runs out of
    time again is checked against the unchanged copy at once."""
    for index, mutant, runner, timeout in runs:
        if results[index] is MutantStatus.TIMEOUT:
            results[index] = await _judge(runner, root, mutant, timeout)
            if results[index] is MutantStatus.TIMEOUT:
                await _still_in_time(runner, root, timeout)


async def _make(runs: Sequence[_Run], results: dict[int, MutantStatus], roots: list[Path]) -> None:
    """Make every one of ``runs`` over the copies, never more copies at work than runs."""
    queue: asyncio.Queue[_Run] = asyncio.Queue()
    for item in runs:
        queue.put_nowait(item)
    busy = roots[: len(runs)]
    await asyncio.gather(*(_work(queue, results, root, alone=len(busy) == 1) for root in busy))
    if len(busy) > 1:
        await _alone(runs, results, roots[0])


async def _seconds(runner: TestRunner, root: Path, timeout: float | None) -> float:
    loop = asyncio.get_running_loop()
    started = loop.time()
    if await runner(root, timeout) is not TestVerdict.PASSED:
        raise BaselineFailed("the tests do not pass on the unchanged copy")
    return loop.time() - started


async def baseline(
    worktree: Path, copy: Path, runner: TestRunner, timeout: float | None = None
) -> float:
    """How many seconds ``runner`` takes on ``copy``, an unchanged copy of ``worktree`` made here,
    where it has to pass (``BaselineFailed``). For a caller that measures the baseline of a
    command once and hands ``run_mutants`` the limit that comes of it, as the engine of the
    mutator keeps one baseline for the files of the same command."""
    await asyncio.to_thread(_fresh_copy, worktree, copy)
    return await _seconds(runner, copy, timeout)


class _Limits:
    """The time a run of each runner is given: the limit the caller measured, or ten times the
    baseline of the runner, never below two seconds. A baseline is run in ``root`` with nothing
    else running, once for each runner (and for the runners equal to it), the first time its
    limit is asked for."""

    def __init__(self, root: Path, baseline_timeout: float | None, given: float | None) -> None:
        self._root, self._baseline_timeout, self._given = root, baseline_timeout, given
        self._measured: dict[TestRunner, float | None] = {}

    async def _measure(self, runner: TestRunner, root: Path) -> None:
        self._measured[runner] = None  # until it passes
        with contextlib.suppress(BaselineFailed):
            seconds = await _seconds(runner, root, self._baseline_timeout)
            self._measured[runner] = max(MIN_TIMEOUT, TIMEOUT_FACTOR * seconds)

    async def of(self, runner: TestRunner) -> float:
        if self._given is not None:
            return self._given
        if runner not in self._measured:
            await self._measure(runner, self._root)
        limit = self._measured[runner]
        if limit is None:
            raise BaselineFailed("the tests do not pass on the unchanged copy")
        return limit

    async def passing(
        self, runners: Sequence[TestRunner], roots: Sequence[Path]
    ) -> set[TestRunner]:
        """Those of ``runners`` that pass on the unchanged copy, and so may judge a mutant.

        Their baselines are run side by side, one in each of ``roots``: the smaller runs may be
        as many as the mutants, and one after the other they would take longer than the mutants
        themselves. A baseline timed beside others is longer than one timed alone, so the limit
        that comes of it is longer too: a mutant is only given more time."""
        if self._given is not None:
            return set(runners)
        queue: asyncio.Queue[TestRunner] = asyncio.Queue()
        for runner in dict.fromkeys(runners):
            if runner not in self._measured:
                queue.put_nowait(runner)

        async def work(root: Path) -> None:
            while True:
                try:
                    runner = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await self._measure(runner, root)

        await asyncio.gather(*(work(root) for root in roots[: queue.qsize()]))
        return {runner for runner in runners if self._measured[runner] is not None}


async def run_mutants(
    worktree: Path,
    scratch: Path,
    mutants: Sequence[Mutant],
    runner: TestRunner,
    *,
    workers: int | None = None,
    baseline_timeout: float | None = None,
    timeout: float | None = None,
) -> tuple[MutationReport, list[int]]:
    """Judge every mutant; the answer is the report and the places of the mutants judged twice.
    ``scratch`` is an empty directory for the copies; the caller removes it.

    ``runner`` is the whole test command. A mutant is judged by its ``first`` runner when it has
    one, and by ``runner`` when it has none or survived the first: those are the ones judged
    twice. As in the mutator each command has its baseline, run on the unchanged copy before the
    first mutant it judges: it must pass, and its duration sets the timeout of those mutants at
    ten times that, but never below two seconds. A ``first`` runner that does not pass on the
    unchanged copy judges nobody: its mutants are judged by ``runner`` alone. A caller that
    measured the baseline hands over ``timeout``, the limit of every run, and no baseline is run
    here.

    With more than one worker, a mutant that timed out is judged once more with nothing else
    running. A timeout with nothing else running is final only if the unchanged copy, run right
    after it by the same runner, still passes in that time; if not, ``MachineTooBusy`` (or
    ``BaselineFailed``) ends the run. As in the mutator there is a worker per processor unless
    ``workers`` says otherwise, never more workers than mutants to run, and with none to run
    nothing is copied.
    """
    results = {i: MutantStatus.UNCOVERED for i, m in enumerate(mutants) if not m.covered}
    to_run = [(i, m) for i, m in enumerate(mutants) if m.covered]
    twice: list[int] = []
    if to_run:
        asked = max(1, workers) if workers is not None else os.cpu_count() or 1
        roots = [scratch / f"worker-{n}" for n in range(min(asked, len(to_run)))]
        # all at once: one after the other, the copies of a worker per processor would take
        # longer than many a run of mutants
        await asyncio.gather(*(asyncio.to_thread(_fresh_copy, worktree, root) for root in roots))
        limits = _Limits(roots[0], baseline_timeout, timeout)
        # a smaller run that does not pass on the unchanged copy would kill for no reason
        passing = await limits.passing([m.first for _, m in to_run if m.first is not None], roots)
        runs, smaller = [], set()
        for i, m in to_run:
            by = runner
            if m.first is not None and m.first in passing:
                by = m.first
                smaller.add(i)
            runs.append((i, m, by, await limits.of(by)))
        await _make(runs, results, roots)
        twice = [i for i, _ in to_run if i in smaller and results[i] is MutantStatus.SURVIVED]
        if twice:
            limit = await limits.of(runner)
            await _make([(i, mutants[i], runner, limit) for i in twice], results, roots)
    report = MutationReport(
        tuple(
            MutantResult(m.file, m.line, m.change, results[i], m.function)
            for i, m in enumerate(mutants)
        )
    )
    return report, twice
