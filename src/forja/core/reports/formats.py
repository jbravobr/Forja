"""Which native report a tool writes, so one reader per format serves every language.

A language adapter says *where* and *in what format* its coverage tool writes; the
core runs the command and hands the file to the reader for that format. Adapters stay pure.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from forja.core.adapters.common import NotSupported
from forja.core.adapters.language import Operation
from forja.core.artifacts.llm_output import check_relative_path


class ReportFormat(StrEnum):
    COVERAGE_PY_JSON = "coverage_py_json"  # Python: coverage.py (json, with contexts)
    JACOCO_XML = "jacoco_xml"  # Java: JaCoCo
    GO_COVERPROFILE = "go_coverprofile"  # Go: go test -coverprofile
    COBERTURA_XML = "cobertura_xml"  # C#: coverlet
    ISTANBUL_JSON = "istanbul_json"  # JavaScript and TypeScript: c8 or istanbul
    # any language: the tests of each line, as the Forja's own piece in the test run writes them
    TEST_LINES_JSON = "test_lines_json"


COVERAGE_FORMATS = frozenset(
    {
        ReportFormat.COVERAGE_PY_JSON,
        ReportFormat.JACOCO_XML,
        ReportFormat.GO_COVERPROFILE,
        ReportFormat.COBERTURA_XML,
        ReportFormat.ISTANBUL_JSON,
    }
)


# Where the run that finds the tests of each line leaves what it writes, in the directory of the
# project: its reports and whatever else it needs. All of it is removed once the reports are read.
TEST_LINES_DIRECTORY = ".forja-test-lines"
# The tests of each line come in one file that says it, or in one coverage report for each test.
TEST_LINES_FORMATS = COVERAGE_FORMATS | {ReportFormat.TEST_LINES_JSON}


def formats_of(operation: Operation) -> frozenset[ReportFormat]:
    """The formats that make sense as the report of an operation; empty for the others."""
    if operation is Operation.COVERAGE:
        return COVERAGE_FORMATS
    if operation is Operation.TEST_LINES:
        return TEST_LINES_FORMATS
    return frozenset()


@dataclass(frozen=True)
class ReportSpec:
    """The report a command leaves behind: its format and its repository-relative POSIX path."""

    format: ReportFormat
    path: str
    # where the sources start, for a report that names a file by package (JaCoCo)
    source_roots: tuple[str, ...] = ()
    # the tests of each line, from a tool that can only write the coverage of one test at a time:
    # ``path`` is a pattern with ``*`` that matches one coverage report for each test, and this
    # file is a JSON object that gives the id of the test for the name of each of those files
    index: str | None = None

    def __post_init__(self) -> None:
        check_relative_path(self.path.replace("*", "x"))
        if self.index is not None:
            check_relative_path(self.index)
        for root in self.source_roots:
            check_relative_path(root)


class ReportingAdapter(Protocol):
    """A language adapter that runs coverage also says what it writes."""

    def report(self, operation: Operation) -> ReportSpec | NotSupported: ...
