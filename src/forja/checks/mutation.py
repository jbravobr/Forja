"""Gate 7 (the mutation part): do the tests notice a change to the lines the attempt wrote?

The lines the diff added are mutated, and so is every other place in a function that holds one
(as in the mutator's differential run). As in unclebob/mutator, coverage only says which lines
a test runs (a mutant on another line is not run), each mutant is judged by the test command,
and the score is killed over killed plus survived; one below the policy's limit reproves.
The check serves every language the mutator serves; the adapter it is given supplies the test
command and the coverage report of its language. Where the adapter has a smaller command for the
file of a mutant (in Go the package of the file, as in the mutator), that one judges the mutant
first; the whole command judges whoever survives it, because the coverage that says a test runs
the line is measured over every package.

Unlike the mutator, which runs the whole command for every mutant, a mutant is judged first by
the tests that run its lines, where the adapter can say which they are and can run only those:
one at a time, the one that killed the same mutant the last time ahead of the others, stopping
at the first that fails. Those tests have to pass by themselves on the unchanged copy before
they judge anybody; if they do not, the whole command judges the mutant. Killed by them, the
mutant is killed. One that survives them is judged by the whole command before it counts, so no
verdict depends on the choice. Which test killed each mutant is kept from one attempt to the
next, and serves that order and nothing else: every mutant is run every time.
"""

import asyncio
import contextlib
import itertools
import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import ClassVar

from forja.checks.base import Undecided, decided, rejected
from forja.checks.changes import Changes, added_lines
from forja.checks.coverage import CoverageShare, run_coverage, run_line_tests
from forja.checks.structure import dialect_of
from forja.checks.tooling import Tools
from forja.core.adapters import (
    LanguageAdapter,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
    build_spec,
)
from forja.core.adapters.scoped import ScopedAdapter
from forja.core.atomic import atomic_write_bytes
from forja.core.gates import (
    Approved,
    Finding,
    FindingKind,
    GateInput,
    GateResult,
    Inconclusive,
)
from forja.core.gates.models import InconclusiveReason
from forja.core.process import Outcome, ProcessIdentity, ProcessRunner
from forja.core.reports.models import CoverageReport, MutantStatus, MutationReport
from forja.mutation.apply import apply_site
from forja.mutation.executor import (
    BaselineFailed,
    Mutant,
    TestsDidNotRun,
    TestVerdict,
    run_mutants,
)
from forja.mutation.model import Site
from forja.mutation.sites import discover, function_spans

DEFAULT_MIN_SCORE = 0.80
LOWER_MIN_SCORE = 0.70  # Python and Go are slower and more fragile (Especificação)
LOWER_LANGUAGES = frozenset({"python", "go"})
BASELINE_TIMEOUT = 600.0
SCRIPT = frozenset({"javascript", "typescript"})  # one family, as in the mutator


def of_language(language: str, files: tuple[str, ...]) -> tuple[str, ...]:
    """The files of ``files`` that the mutation rules of ``language`` can change."""
    found = []
    for file in files:
        dialect = dialect_of(file)
        if dialect is None:
            continue
        same = dialect.language == language or {dialect.language, language} <= SCRIPT
        if same:
            found.append(file)
    return tuple(found)


def default_min_score(language: str) -> float:
    return LOWER_MIN_SCORE if language in LOWER_LANGUAGES else DEFAULT_MIN_SCORE


def min_score(data: GateInput, language: str) -> float:
    thresholds = data.values.get("thresholds")
    per_language = thresholds.get(language) if isinstance(thresholds, dict) else None
    value = per_language.get("min_score") if isinstance(per_language, dict) else None
    ok = isinstance(value, int | float) and not isinstance(value, bool) and 0 < value <= 1
    return float(value) if ok and isinstance(value, int | float) else default_min_score(language)


def worker_count(data: GateInput) -> int | None:
    limits = data.values.get("limits")
    value = limits.get("workers") if isinstance(limits, dict) else None
    ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
    return value if ok and isinstance(value, int) else None


def changed_lines(changes: Changes, files: tuple[str, ...]) -> dict[str, frozenset[int]]:
    """The lines of ``files`` the attempt added, by file."""
    found: dict[str, set[int]] = {}
    for line in added_lines(changes.patch):
        if line.path in files and line.number is not None:
            found.setdefault(line.path, set()).add(line.number)
    return {path: frozenset(numbers) for path, numbers in found.items()}


def touched_functions(source: str, path: str, wanted: frozenset[int]) -> frozenset[str]:
    """The names of the functions that hold a line of ``wanted``: their text changed."""
    return frozenset(
        name
        for name, first, last in function_spans(source, path)
        if any(first <= number <= last for number in wanted)
    )


def build_mutants(
    worktree: Path,
    lines: Mapping[str, frozenset[int]],
    coverage: CoverageReport,
    whole_functions: bool = True,
) -> list[Mutant]:
    """One mutant per place on a changed line where the rules allow a change. With
    ``whole_functions`` (the default, as in the mutator's differential run) also one per place
    anywhere in a function that holds a changed line. A file the rules cannot read stops the
    measurement (``Undecided``): no mutant of it would otherwise look like every mutant killed."""
    mutants: list[Mutant] = []
    for path, wanted in sorted(lines.items()):
        try:
            source = (worktree / path).read_text(encoding="utf-8")
            sites = discover(source, path)
            touched = touched_functions(source, path, wanted) if whole_functions else frozenset()
        except (OSError, SyntaxError, ValueError) as error:
            # the build gate has already passed: the file is good and the rules cannot read it,
            # so nothing is known about its new lines, which is not the same as their being tested
            raise Undecided(
                Inconclusive(
                    reason=InconclusiveReason.UNSUPPORTED_CONSTRUCT,
                    detail=f"mutation: the rules cannot read {path} ({error})",
                )
            ) from None
        for site in sites:
            if site.line not in wanted and site.function not in touched:
                continue
            mutated = apply_site(source, site).encode("utf-8")
            covered = any(coverage.is_covered(path, line) for line in site.lines)
            mutants.append(
                Mutant(
                    path,
                    site.line,
                    site.description,
                    site.function,
                    mutated,
                    covered,
                    id=mutant_id(path, site),
                    lines=site.lines,
                )
            )
    return mutants


def mutant_id(path: str, site: Site) -> str:
    """What names a mutant from one attempt to the next: the file, the function, where the token
    is and the change (the id the mutator gives it)."""
    return json.dumps(
        [path, site.function, site.start, site.end, site.original, site.mutant],
        separators=(",", ":"),
    )


HISTORY_VERSION = 1


def load_killers(path: Path | None) -> dict[str, str]:
    """The test that killed each mutant the last time, by the id of the mutant. A file that is
    not there, or cannot be read, is no history: it only orders the tests."""
    if path is None:
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    killers = document.get("killers") if isinstance(document, dict) else None
    if not isinstance(killers, dict) or document.get("version") != HISTORY_VERSION:
        return {}
    return {
        mutant: test
        for mutant, test in killers.items()
        if isinstance(mutant, str) and isinstance(test, str) and test
    }


def save_killers(path: Path | None, killers: Mapping[str, str]) -> None:
    if path is None:
        return
    document = {"version": HISTORY_VERSION, "killers": dict(sorted(killers.items()))}
    # unwritten, the next attempt runs the tests in the order of their ids, and nothing else changes
    with contextlib.suppress(OSError):
        atomic_write_bytes(path, json.dumps(document, indent=1).encode("utf-8"))


def untested_lines(
    lines: Mapping[str, frozenset[int]], mutants: list[Mutant], coverage: CoverageReport
) -> dict[str, list[int]]:
    """Added lines no test runs: the tool's missed lines, and mutable lines of unseen files."""
    found: dict[str, set[int]] = {}
    for path, wanted in lines.items():
        seen = coverage.of(path)
        if seen is not None:
            found.setdefault(path, set()).update(wanted & seen.missed)
    for mutant in mutants:
        if not mutant.covered:
            found.setdefault(mutant.file, set()).add(mutant.line)
    return {path: sorted(numbers) for path, numbers in sorted(found.items()) if numbers}


class _Quiet:
    """Thousands of mutant runs are not events anyone reads."""

    def intent(self, operation_id: str, details: Mapping[str, object]) -> None:
        pass

    def started(self, operation_id: str, identity: ProcessIdentity) -> None:
        pass

    def progress(self, operation_id: str, channel: str, line: str) -> None:
        pass

    def effect(self, operation_id: str, details: Mapping[str, object]) -> None:
        pass


FAILED = ("failed", "error")


@dataclass
class Killer:
    """The test that failed in the last run of a runner made for one mutant, when it is known."""

    test: str | None = None


def failed_test(adapter: LanguageAdapter, output: ToolOutput, asked: tuple[str, ...]) -> str | None:
    """The first of the tests ``asked`` for that the output of their run shows as failed, or
    ``None`` when it shows none of them (the code did not build, the output cannot be read).

    The ids the tests were asked by are those of the project, so the output is read by the
    adapter of the project itself. Where the tool reports a test by an id it cannot be asked
    by (a case of a parametrized test, in a tool that only runs the whole method), the adapter
    says with ``selector`` which id asks for it."""
    inner = adapter.inner if isinstance(adapter, ScopedAdapter) else adapter
    parsed = inner.parse(Operation.TEST, output)
    if not isinstance(parsed, Parsed):
        return None
    selector = getattr(inner, "selector", None)
    failed = {
        str(selector(draft.target.id)) if callable(selector) else draft.target.id
        for draft in parsed.drafts
        if draft.measurement == "test.outcome" and draft.value in FAILED
    }
    return next((test for test in asked if test in failed), None)


@dataclass(frozen=True, eq=False)
class AdapterRunner:
    """Runs the adapter's test command in a copy of the repository: the whole one, with
    ``mutated`` the one the adapter has for a mutant of that file, or with ``tests`` those tests
    and no other. A runner with ``tests`` is made for one mutant, and leaves in ``killer`` the
    test that failed.

    The executor keeps one baseline for the runners that are equal. Two are equal when they ask
    the same adapter for the same thing, so the mutants of a file that the same tests judge, in
    the same order, share the baseline though each has its runner."""

    __test__: ClassVar[bool] = False  # not a test class, whatever its name says to pytest

    adapter: LanguageAdapter
    host_env: Mapping[str, str]
    logs: Path
    ids: "itertools.count[int]" = field(default_factory=itertools.count)
    mutated: str | None = None
    tests: tuple[str, ...] = ()
    killer: Killer = field(default_factory=Killer)

    def _asked(self) -> tuple[int, str | None, tuple[str, ...]]:
        return (id(self.adapter), self.mutated, self.tests)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, AdapterRunner) and self._asked() == other._asked()

    def __hash__(self) -> int:
        return hash(self._asked())

    def command(self, root: Path) -> tuple[str, ...] | None:
        """What would run in ``root``; ``None`` when the adapter cannot plan it."""
        plan = self.adapter.plan(Operation.TEST, self._request(root))
        return None if isinstance(plan, NotSupported) else plan.argv

    def _request(self, root: Path) -> OperationRequest:
        return OperationRequest(root=root, rerun=True, mutated=self.mutated, tests=self.tests)

    async def __call__(self, root: Path, timeout: float | None) -> TestVerdict:
        plan = self.adapter.plan(Operation.TEST, self._request(root))
        if isinstance(plan, NotSupported):
            raise TestsDidNotRun(plan.reason)
        # a stale bytecode file could run the original code in place of the mutant
        env = {**plan.env_set, "PYTHONDONTWRITEBYTECODE": "1"}
        # the baseline and every mutant yield to the rest of the machine: a run of mutants
        # takes every processor for minutes, and nobody waits on one of them at the keyboard
        spec = build_spec(
            replace(plan, env_set=env), self.host_env, timeout=timeout, low_priority=True
        )
        number = next(self.ids)
        log = self.logs / f"mutant-{number}.log"
        self.logs.mkdir(parents=True, exist_ok=True)
        try:
            result = await ProcessRunner(_Quiet()).run(f"mutant:{number}", spec, log)
            failed = result.outcome is Outcome.EXITED and result.exit_code != 0
            if self.tests:
                output = ToolOutput(result.exit_code, log, plan.cwd)
                found = failed_test(self.adapter, output, self.tests) if failed else None
                self.killer.test = found
        finally:
            log.unlink(missing_ok=True)
        if result.outcome is Outcome.TIMED_OUT:
            return TestVerdict.TIMED_OUT
        if result.outcome is not Outcome.EXITED:
            raise TestsDidNotRun(result.outcome.value)
        return TestVerdict.FAILED if failed else TestVerdict.PASSED


def in_order(tests: frozenset[str], killer: str | None) -> tuple[str, ...]:
    """``tests`` in the order they are asked to run: the one that killed the mutant the last
    time ahead, the others by their ids, so the same request gives the same command."""
    ahead = (killer,) if killer is not None and killer in tests else ()
    return (*ahead, *sorted(tests - set(ahead)))


def _by_its_tests(
    runner: AdapterRunner,
    worktree: Path,
    mutant: Mutant,
    coverage: CoverageReport,
    killers: Mapping[str, str],
    whole: tuple[str, ...] | None,
) -> Mutant | None:
    """``mutant`` judged first by the tests that run its lines; ``None`` where the report names
    no test for them, or the adapter cannot run only those (it has no such filter, or they are
    too many for one command)."""
    tests = coverage.tests_of(mutant.file, mutant.lines) if mutant.covered else frozenset()
    # the ids come from a report of the project and go into a command line and its environment:
    # one with a control character in it (a line break, a NUL) is not handed to any tool
    if not tests or any(ord(character) < 32 for test in tests for character in test):
        return None
    asked = in_order(tests, killers.get(mutant.id))
    aimed = replace(runner, mutated=mutant.file, tests=asked, killer=Killer())
    command = aimed.command(worktree)
    if command is None or command == whole:
        return None
    return replace(mutant, first=aimed)


def judged_first(
    runner: AdapterRunner,
    worktree: Path,
    mutants: list[Mutant],
    coverage: CoverageReport | None = None,
    killers: Mapping[str, str] | None = None,
) -> list[Mutant]:
    """``mutants``, each with the runner that judges it first, where there is one.

    With a ``coverage`` that names the tests of the lines of a mutant, and an adapter that can
    run only those, they judge it first, in the order of ``in_order``, each mutant with a runner
    of its own (equal runners share the baseline). Any other mutant gets the runner of the
    smaller command the adapter has for its file, where it has one: mutants whose files get the
    same command share the runner, and with it the baseline. A mutant whose file gets the whole
    command is left as it is."""
    whole = runner.command(worktree)
    smaller: dict[tuple[str, ...], AdapterRunner] = {}
    found = []
    for mutant in mutants:
        by_tests = (
            None
            if coverage is None
            else _by_its_tests(runner, worktree, mutant, coverage, killers or {}, whole)
        )
        if by_tests is not None:
            found.append(by_tests)
            continue
        aimed = replace(runner, mutated=mutant.file)
        command = aimed.command(worktree)
        if command is None or command == whole:
            found.append(mutant)
        else:
            found.append(replace(mutant, first=smaller.setdefault(command, aimed)))
    return found


def killers_of(mutants: list[Mutant]) -> dict[str, str]:
    """After the run, the test that killed each mutant judged first by the tests of its lines."""
    found = {}
    for mutant in mutants:
        first = mutant.first
        if isinstance(first, AdapterRunner) and first.tests and first.killer.test is not None:
            found[mutant.id] = first.killer.test
    return found


def _failed(detail: str) -> Undecided:
    return Undecided(Inconclusive(reason=InconclusiveReason.TOOL_FAILED, detail=detail))


@dataclass(frozen=True)
class MutationCheck:
    tools: Tools
    adapter: LanguageAdapter
    worktree: Path
    changes: Changes
    files: tuple[str, ...]  # production files the attempt changed
    host_env: Mapping[str, str]
    language: str = "python"
    id: str = "mutation"
    gate: int = 7
    requires: frozenset[str] = frozenset({"coverage"})
    share: CoverageShare | None = None
    # where the test that killed each mutant is kept from one attempt to the next; ``None`` keeps
    # none
    history: Path | None = None

    async def evaluate(self, data: GateInput) -> GateResult:
        return await decided(self._judge(data))

    async def _judge(self, data: GateInput) -> GateResult:
        lines = changed_lines(self.changes, of_language(self.language, self.files))
        if not lines:
            return Approved()
        share = self._share()
        coverage = await run_coverage(self.tools, self.adapter, self.worktree, data, share)
        mutants = build_mutants(self.worktree, lines, coverage)
        # a line the attempt did not write is not its to cover: its mutant is only not run
        written = [m for m in mutants if m.line in lines.get(m.file, frozenset())]
        untested = untested_lines(lines, written, coverage)
        if untested:
            return self._unprotected(untested)
        if not mutants:
            return Approved()
        report = await self._run(data, mutants, share)
        return self._verdict(report, min_score(data, self.language))

    def _share(self) -> CoverageShare:
        """Where the coverage of this judgment is kept: the one shared with the other checks, or
        one of its own, so the tests are not measured again to learn the tests of each line."""
        return self.share if self.share is not None else CoverageShare()

    async def _named(
        self, data: GateInput, mutants: list[Mutant], share: CoverageShare
    ) -> CoverageReport | None:
        """The coverage that names the tests of each line, measured only when a mutant will run;
        ``None`` when none will."""
        if not any(mutant.covered for mutant in mutants):
            return None
        return await run_line_tests(self.tools, self.adapter, self.worktree, data, share)

    async def protects(self, data: GateInput, lines: Mapping[str, frozenset[int]]) -> bool:
        """Whether the tests protect ``lines`` as the gate asks of the lines an attempt adds: a
        test runs every one of them and the score of their mutants reaches the limit. The planner
        asks it of code nobody changed yet. Raises ``Undecided`` where the gate is inconclusive."""
        share = self._share()
        coverage = await run_coverage(self.tools, self.adapter, self.worktree, data, share)
        mutants = build_mutants(self.worktree, lines, coverage, whole_functions=False)
        if untested_lines(lines, mutants, coverage):
            return False
        if not mutants:
            return True
        score = (await self._run(data, mutants, share)).score
        return score is None or score >= min_score(data, self.language)

    async def _run(
        self, data: GateInput, mutants: list[Mutant], share: CoverageShare
    ) -> MutationReport:
        named = await self._named(data, mutants, share)
        killers = load_killers(self.history)
        with tempfile.TemporaryDirectory(prefix="forja-mutants-", ignore_cleanup_errors=True) as d:
            runner = AdapterRunner(self.adapter, self.host_env, Path(d) / "logs")
            judged = judged_first(runner, self.worktree, mutants, named, killers)
            try:
                report, _ = await run_mutants(
                    self.worktree,
                    Path(d) / "copies",
                    judged,
                    runner,
                    workers=worker_count(data),
                    baseline_timeout=BASELINE_TIMEOUT,
                )
            except BaselineFailed:
                raise _failed("mutation: the tests do not pass on the unchanged copy") from None
            except TestsDidNotRun as error:
                raise _failed(f"mutation: the tests could not run ({error})") from None
            found = killers_of(judged)
            if found:
                await asyncio.to_thread(save_killers, self.history, {**killers, **found})
            return report

    def _unprotected(self, untested: Mapping[str, list[int]]) -> GateResult:
        """Without a test there is nothing to mutate against: the test author must write one."""
        return rejected(
            [
                Finding(
                    message="no test runs this new line; a test for it is needed before mutation",
                    target=f"{path}:{number}",
                    kind=FindingKind.UNTESTED_LINE,
                )
                for path, numbers in untested.items()
                for number in numbers
            ]
        )

    def _verdict(self, report: MutationReport, limit: float) -> GateResult:
        score = report.score
        if score is None or score >= limit:
            return Approved()
        bad = report.with_status(MutantStatus.SURVIVED)
        findings = [
            Finding(
                message="the tests do not notice enough changes to the new code",
                target="mutation",
                measured=round(score, 2),
                limit=limit,
            )
        ]
        findings += [
            Finding(
                message=f"change not noticed ({m.status.value}): {m.change}",
                target=f"{m.file}:{m.line}",
            )
            for m in bad
        ]
        return rejected(findings)
