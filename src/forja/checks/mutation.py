"""Gate 7 (the mutation part): do the tests notice a change to the lines the attempt wrote?

The lines the diff added are mutated, and so is every other place in a function that holds one
(as in the mutator's differential run). As in unclebob/mutator, coverage only says which lines
a test runs (a mutant on another line is not run), each mutant is judged by the whole test
command, and the score is killed over killed plus survived; one below the policy's limit reproves.
The check serves every language the mutator serves; the adapter it is given supplies the test
command and the coverage report of its language.
"""

import itertools
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import ClassVar

from forja.checks.base import Undecided, decided, rejected
from forja.checks.changes import Changes, added_lines
from forja.checks.coverage import CoverageShare, run_coverage
from forja.checks.structure import dialect_of
from forja.checks.tooling import Tools
from forja.core.adapters import (
    LanguageAdapter,
    NotSupported,
    Operation,
    OperationRequest,
    build_spec,
)
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
                Mutant(path, site.line, site.description, site.function, mutated, covered)
            )
    return mutants


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


@dataclass(frozen=True)
class AdapterRunner:
    """Runs the adapter's whole test command in a copy of the repository."""

    __test__: ClassVar[bool] = False  # not a test class, whatever its name says to pytest

    adapter: LanguageAdapter
    host_env: Mapping[str, str]
    logs: Path
    ids: "itertools.count[int]" = field(default_factory=itertools.count)

    async def __call__(self, root: Path, timeout: float | None) -> TestVerdict:
        plan = self.adapter.plan(Operation.TEST, OperationRequest(root=root, rerun=True))
        if isinstance(plan, NotSupported):
            raise TestsDidNotRun(plan.reason)
        # a stale bytecode file could run the original code in place of the mutant
        env = {**plan.env_set, "PYTHONDONTWRITEBYTECODE": "1"}
        spec = build_spec(replace(plan, env_set=env), self.host_env, timeout=timeout)
        number = next(self.ids)
        log = self.logs / f"mutant-{number}.log"
        self.logs.mkdir(parents=True, exist_ok=True)
        try:
            result = await ProcessRunner(_Quiet()).run(f"mutant:{number}", spec, log)
        finally:
            log.unlink(missing_ok=True)
        if result.outcome is Outcome.TIMED_OUT:
            return TestVerdict.TIMED_OUT
        if result.outcome is not Outcome.EXITED:
            raise TestsDidNotRun(result.outcome.value)
        return TestVerdict.PASSED if result.exit_code == 0 else TestVerdict.FAILED


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

    async def evaluate(self, data: GateInput) -> GateResult:
        return await decided(self._judge(data))

    async def _judge(self, data: GateInput) -> GateResult:
        lines = changed_lines(self.changes, of_language(self.language, self.files))
        if not lines:
            return Approved()
        coverage = await run_coverage(self.tools, self.adapter, self.worktree, data, self.share)
        mutants = build_mutants(self.worktree, lines, coverage)
        # a line the attempt did not write is not its to cover: its mutant is only not run
        written = [m for m in mutants if m.line in lines.get(m.file, frozenset())]
        untested = untested_lines(lines, written, coverage)
        if untested:
            return self._unprotected(untested)
        if not mutants:
            return Approved()
        report = await self._run(data, mutants)
        return self._verdict(report, min_score(data, self.language))

    async def protects(self, data: GateInput, lines: Mapping[str, frozenset[int]]) -> bool:
        """Whether the tests protect ``lines`` as the gate asks of the lines an attempt adds: a
        test runs every one of them and the score of their mutants reaches the limit. The planner
        asks it of code nobody changed yet. Raises ``Undecided`` where the gate is inconclusive."""
        coverage = await run_coverage(self.tools, self.adapter, self.worktree, data, self.share)
        mutants = build_mutants(self.worktree, lines, coverage, whole_functions=False)
        if untested_lines(lines, mutants, coverage):
            return False
        if not mutants:
            return True
        score = (await self._run(data, mutants)).score
        return score is None or score >= min_score(data, self.language)

    async def _run(self, data: GateInput, mutants: list[Mutant]) -> MutationReport:
        with tempfile.TemporaryDirectory(prefix="forja-mutants-", ignore_cleanup_errors=True) as d:
            runner = AdapterRunner(self.adapter, self.host_env, Path(d) / "logs")
            try:
                return await run_mutants(
                    self.worktree,
                    Path(d) / "copies",
                    mutants,
                    runner,
                    workers=worker_count(data),
                    baseline_timeout=BASELINE_TIMEOUT,
                )
            except BaselineFailed:
                raise _failed("mutation: the tests do not pass on the unchanged copy") from None
            except TestsDidNotRun as error:
                raise _failed(f"mutation: the tests could not run ({error})") from None

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
