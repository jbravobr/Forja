"""What coverage and mutation tools report, in one shape for every language.

Readers (one per native report format) produce these; checks consume them. Paths are
repository-relative POSIX paths: turning a tool's own paths into that is the reader's job.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from forja.core.artifacts.llm_output import check_relative_path


def _line(value: int) -> int:
    if value < 1:
        raise ValueError(f"line numbers start at 1, got {value}")
    return value


@dataclass(frozen=True)
class FileCoverage:
    """Executable lines of one file, split into hit and not hit.

    A line in neither set is not executable (blank, comment, signature): it has no coverage to
    speak of. ``tests`` says which tests hit a line, when the tool records it (coverage.py with
    per-test contexts does); the mutation executor uses it to run only the tests that matter.
    """

    covered: frozenset[int]
    missed: frozenset[int] = frozenset()
    tests: Mapping[int, frozenset[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for number in (*self.covered, *self.missed, *self.tests):
            _line(number)
        if self.covered & self.missed:
            raise ValueError("a line cannot be both covered and missed")
        if not set(self.tests) <= self.covered:
            raise ValueError("only covered lines have tests")

    @property
    def executable(self) -> frozenset[int]:
        return self.covered | self.missed

    def fraction(self, lines: Iterable[int] | None = None) -> float | None:
        """Share of executable lines hit, over ``lines`` or the whole file; ``None`` if none of
        them is executable (nothing to measure, which is not the same as 0 %)."""
        wanted = self.executable if lines is None else self.executable & set(lines)
        if not wanted:
            return None
        return len(wanted & self.covered) / len(wanted)


@dataclass(frozen=True)
class CoverageReport:
    files: Mapping[str, FileCoverage]

    def __post_init__(self) -> None:
        for path in self.files:
            check_relative_path(path)

    def of(self, path: str) -> FileCoverage | None:
        """``None`` when the tool never saw the file, which is not the same as 0 % covered."""
        return self.files.get(path)

    def is_covered(self, path: str, line: int) -> bool:
        file = self.files.get(path)
        return file is not None and line in file.covered

    @property
    def names_tests(self) -> bool:
        """Whether the report says, of any line, which tests run it."""
        return any(file.tests for file in self.files.values())

    def tests_of(self, path: str, lines: Iterable[int]) -> frozenset[str]:
        """The tests that run any of ``lines`` of ``path``, as far as the report says."""
        file = self.files.get(path)
        if file is None:
            return frozenset()
        return frozenset().union(*(file.tests.get(line, frozenset()) for line in lines))

    def with_tests(self, found: Mapping[str, Mapping[int, frozenset[str]]]) -> "CoverageReport":
        """This report, with ``found`` added to the tests of each line. Only of the lines the
        report has as covered: it is the report that says a test runs a line."""
        files = dict(self.files)
        for path, lines in found.items():
            file = files.get(path)
            if file is None:
                continue
            tests = {
                line: file.tests.get(line, frozenset()) | lines.get(line, frozenset())
                for line in (set(file.tests) | set(lines)) & file.covered
            }
            files[path] = FileCoverage(file.covered, file.missed, tests)
        return CoverageReport(files)


class MutantStatus(StrEnum):
    KILLED = "killed"  # a test failed: the change was noticed
    SURVIVED = "survived"  # every test passed: the change went unnoticed
    UNCOVERED = "uncovered"  # no test runs the line: not run, and left out of the score
    TIMEOUT = "timeout"  # the tests did not finish; tools differ on whether this counts as killed


@dataclass(frozen=True)
class MutantResult:
    """One change made to the code and what the tests did about it.

    ``change`` is the tool's own text for the change (``"<= -> <"``); suppressions match on it.
    """

    file: str
    line: int
    change: str
    status: MutantStatus
    function: str | None = None  # qualified name of the enclosing function, when known

    def __post_init__(self) -> None:
        check_relative_path(self.file)
        _line(self.line)
        if not self.change:
            raise ValueError("a mutant needs the text of its change")


@dataclass(frozen=True)
class MutationReport:
    mutants: tuple[MutantResult, ...]

    def with_status(self, status: MutantStatus) -> tuple[MutantResult, ...]:
        return tuple(m for m in self.mutants if m.status is status)

    def on_lines(self, lines: Mapping[str, frozenset[int]]) -> "MutationReport":
        """Only the mutants on the given lines of the given files (the lines an attempt changed)."""
        return MutationReport(
            tuple(m for m in self.mutants if m.line in lines.get(m.file, frozenset()))
        )

    @property
    def score(self) -> float | None:
        """Killed share of the mutants that ran, as in unclebob/mutator: killed over killed plus
        survived. A timeout counts as killed; an uncovered mutant was not run and is left out.
        ``None`` if none ran."""
        killed = sum(m.status in (MutantStatus.KILLED, MutantStatus.TIMEOUT) for m in self.mutants)
        ran = killed + sum(m.status is MutantStatus.SURVIVED for m in self.mutants)
        return killed / ran if ran else None
