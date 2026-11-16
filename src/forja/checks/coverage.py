"""Runs the adapter's coverage tool and reads what it wrote; mutation and CRAP share it."""

import asyncio
import contextlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from forja.checks.base import Undecided, overrides_from
from forja.checks.tooling import Tools
from forja.core.adapters import (
    CommandPlan,
    LanguageAdapter,
    Operation,
    OperationRequest,
    plan_with_override,
)
from forja.core.adapters.scoped import ScopedAdapter
from forja.core.gates import GateInput, Inconclusive, NotApplicable
from forja.core.gates.models import InconclusiveReason
from forja.core.reports.formats import TEST_LINES_DIRECTORY, ReportSpec
from forja.core.reports.models import CoverageReport, FileCoverage
from forja.core.reports.readers import read_coverage, read_test_lines
from forja.core.reports.reading import ReportError
from forja.core.reports.test_lines import LineTests


def _failed(detail: str) -> Undecided:
    return Undecided(Inconclusive(reason=InconclusiveReason.TOOL_FAILED, detail=detail))


@dataclass
class CoverageShare:
    """Holds the coverage of one attempt so that two checks do not run the tests twice."""

    report: CoverageReport | None = None
    # the tests of each line were asked for, whatever came of it: they are not asked for twice
    line_tests: bool = False


async def run_coverage(
    tools: Tools,
    adapter: LanguageAdapter,
    worktree: Path,
    data: GateInput,
    share: CoverageShare | None = None,
) -> CoverageReport:
    """The coverage of the tests over ``worktree``; the report file is removed after it is read.

    With a ``share``, the first call measures and the later ones reuse the result.
    """
    if share is not None and share.report is not None:
        return share.report
    measured = await _measure(tools, adapter, worktree, data)
    if share is not None:
        share.report = measured
    return measured


async def run_line_tests(
    tools: Tools,
    adapter: LanguageAdapter,
    worktree: Path,
    data: GateInput,
    share: CoverageShare | None = None,
) -> CoverageReport:
    """The coverage of ``run_coverage``, saying also which tests run each line where the adapter
    can find it out: from the coverage report itself (coverage.py) or from a run of its own
    (``Operation.TEST_LINES``), made here once.

    The tests of a line only choose who judges a mutant first, and never change a verdict. So a
    run of them that fails, or a report of them that cannot be read, leaves the coverage as it
    was: nothing is chosen, and every mutant is judged by every test.
    """
    coverage = await run_coverage(tools, adapter, worktree, data, share)
    if coverage.names_tests or (share is not None and share.line_tests):
        return coverage
    found = await _line_tests(tools, adapter, worktree)
    named = coverage.with_tests(found) if found else coverage
    if share is not None:
        share.report, share.line_tests = named, True
    return named


def _spec_of(adapter: LanguageAdapter, operation: Operation) -> ReportSpec | None:
    report = getattr(adapter, "report", None)
    spec = report(operation) if callable(report) else None
    return spec if isinstance(spec, ReportSpec) else None


def _project_of(adapter: LanguageAdapter) -> ScopedAdapter | None:
    return adapter if isinstance(adapter, ScopedAdapter) and adapter.base != "." else None


async def _line_tests(tools: Tools, adapter: LanguageAdapter, worktree: Path) -> LineTests:
    spec = _spec_of(adapter, Operation.TEST_LINES)
    plan = adapter.plan(Operation.TEST_LINES, OperationRequest(root=worktree))
    if spec is None or not isinstance(plan, CommandPlan):
        return {}
    index = None if spec.index is None else worktree / spec.index
    project = _project_of(adapter)
    root = project.directory(worktree) if project else worktree
    source_roots = (
        tuple(project.local(r) for r in spec.source_roots) if project else spec.source_roots
    )
    paths: list[Path] = []
    try:
        execution = await tools.run("test_lines", plan)
        paths = report_paths(worktree, spec.path)
        if not execution.finished or execution.result.exit_code != 0:
            return {}
        found = read_test_lines(
            spec.format,
            {path.name: path.read_text(encoding="utf-8") for path in paths},
            root,
            source_roots,
            None if index is None else index.read_text(encoding="utf-8"),
        )
        if project is None:
            return found
        return {project.repository(name): lines for name, lines in found.items()}
    except (OSError, ValueError):  # a report that is not text is a ``ValueError`` too
        return {}
    finally:
        for path in (*paths, *(() if index is None else (index,))):
            with contextlib.suppress(OSError):  # a file some other program holds stops nothing
                path.unlink(missing_ok=True)  # the reports are not part of the attempt
        # nor is whatever else the run left beside them
        await asyncio.to_thread(shutil.rmtree, root / TEST_LINES_DIRECTORY, ignore_errors=True)


async def _measure(
    tools: Tools, adapter: LanguageAdapter, worktree: Path, data: GateInput
) -> CoverageReport:
    spec = _spec_of(adapter, Operation.COVERAGE)
    if spec is None:
        raise Undecided(NotApplicable(missing_capability=Operation.COVERAGE.value))
    request = OperationRequest(root=worktree)
    overrides = overrides_from(adapter, data.values)
    plan = plan_with_override(adapter, Operation.COVERAGE, request, overrides)
    if not isinstance(plan, CommandPlan):
        raise Undecided(NotApplicable(missing_capability=Operation.COVERAGE.value))
    execution = await tools.run("coverage", plan)
    if not execution.finished:
        raise Undecided(execution.inconclusive("coverage"))
    paths = report_paths(worktree, spec.path)
    # what a tool writes is relative to the project's directory: read it from there, then name
    # the files as the repository does
    project = _project_of(adapter)
    root = project.directory(worktree) if project else worktree
    source_roots = (
        tuple(project.local(r) for r in spec.source_roots) if project else spec.source_roots
    )
    try:
        if execution.result.exit_code != 0:
            raise _failed(f"coverage: the tool ended with {execution.result.exit_code}")
        if not paths:
            raise OSError(f"{spec.path} was not written")
        merged = merge_coverage(
            [
                read_coverage(spec.format, path.read_text(encoding="utf-8"), root, source_roots)
                for path in paths
            ]
        )
        if project is None:
            return merged
        return CoverageReport(
            {project.repository(name): file for name, file in merged.files.items()}
        )
    except (OSError, ReportError) as error:
        raise _failed(f"coverage: the report cannot be read ({error})") from None
    finally:
        for path in paths:
            path.unlink(missing_ok=True)  # the report is not part of the attempt


def report_paths(worktree: Path, pattern: str) -> list[Path]:
    """The report files; a pattern with ``*`` stands for a tool that puts them in a directory it
    names itself (a run id), one report per test project. A match that is a link, or that leaves
    the worktree through one, is not a report and is never read or removed."""
    if "*" not in pattern:
        return [worktree / pattern]
    inside = worktree.resolve()
    return sorted(
        path
        for path in worktree.glob(pattern)
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(inside)
    )


def merge_coverage(reports: list[CoverageReport]) -> CoverageReport:
    """A line is covered if any report covers it; the tests that ran it add up."""
    if len(reports) == 1:
        return reports[0]
    merged: dict[str, FileCoverage] = {}
    for report in reports:
        for name, file in report.files.items():
            seen = merged.get(name)
            if seen is None:
                merged[name] = file
                continue
            covered = seen.covered | file.covered
            tests = {
                line: seen.tests.get(line, frozenset()) | file.tests.get(line, frozenset())
                for line in set(seen.tests) | set(file.tests)
            }
            merged[name] = FileCoverage(
                covered=covered, missed=(seen.missed | file.missed) - covered, tests=tests
            )
    return CoverageReport(merged)
