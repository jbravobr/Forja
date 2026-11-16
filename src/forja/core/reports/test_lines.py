"""Which tests run each line, read from what a tool left: one file that says it, or one coverage
report for each test.

The answer only serves to choose the tests that judge a mutant first, and whoever survives them
is judged by every test: a test missing here costs time, never a verdict.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from forja.core.reports.models import CoverageReport
from forja.core.reports.reading import MAX_LINE, ReportError, load_json, number, resolve_path

# file -> line -> the ids of the tests that run it
LineTests = Mapping[str, Mapping[int, frozenset[str]]]


def _frozen(found: Mapping[str, Mapping[int, Iterable[str]]]) -> LineTests:
    return {
        path: {line: frozenset(tests) for line, tests in sorted(lines.items())}
        for path, lines in sorted(found.items())
    }


def read_test_lines_json(text: str, root: Path, source_roots: Sequence[str] = ()) -> LineTests:
    """The format of the Forja: ``{"<file>": {"<line>": ["<test id>", ...]}}``."""
    document = load_json(text)
    if not isinstance(document, dict):
        raise ReportError("the tests of each line must be an object of files")
    found: dict[str, dict[int, set[str]]] = {}
    for raw, lines in document.items():
        if not isinstance(lines, dict):
            raise ReportError(f"the tests of each line: {raw!r} is not an object of lines")
        of_file = found.setdefault(resolve_path(str(raw), root, source_roots), defaultdict(set))
        for line, tests in lines.items():
            where = number(line, "line")
            if not 1 <= where <= MAX_LINE:
                raise ReportError(f"a line number out of range: {where}")
            if not isinstance(tests, list) or not all(isinstance(t, str) and t for t in tests):
                raise ReportError(f"the tests of line {where} of {raw!r} are not a list of ids")
            of_file[where].update(tests)
    return _frozen(found)


def read_index(text: str) -> dict[str, str]:
    """The id of the test each report file holds: ``{"<file name>": "<test id>"}``."""
    document = load_json(text)
    if not isinstance(document, dict) or not all(
        isinstance(test, str) and test for test in document.values()
    ):
        raise ReportError("the index of the reports must be an object of test ids")
    return dict(document)


def tests_by_line(reports: Iterable[tuple[str, CoverageReport]]) -> LineTests:
    """From the coverage of each test, alone, the tests of each line."""
    found: dict[str, dict[int, set[str]]] = {}
    for test, report in reports:
        for path, file in report.files.items():
            of_file = found.setdefault(path, defaultdict(set))
            for line in file.covered:
                of_file[line].add(test)
    return _frozen(found)
