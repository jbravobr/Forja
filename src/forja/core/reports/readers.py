"""One entry point per kind of report: pick the reader by format, and fail only with
``ReportError``, whatever the file holds."""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from forja.core.reports.coverage_readers import (
    read_cobertura_xml,
    read_coverage_py_json,
    read_go_coverprofile,
    read_istanbul_json,
    read_jacoco_xml,
)
from forja.core.reports.formats import ReportFormat
from forja.core.reports.models import CoverageReport
from forja.core.reports.reading import MAX_REPORT_CHARS, ReportError
from forja.core.reports.test_lines import LineTests, read_index, read_test_lines_json, tests_by_line

_COVERAGE: dict[ReportFormat, Callable[[str, Path, Sequence[str]], CoverageReport]] = {
    ReportFormat.COVERAGE_PY_JSON: read_coverage_py_json,
    ReportFormat.JACOCO_XML: read_jacoco_xml,
    ReportFormat.GO_COVERPROFILE: read_go_coverprofile,
    ReportFormat.COBERTURA_XML: read_cobertura_xml,
    ReportFormat.ISTANBUL_JSON: read_istanbul_json,
}


def _guarded[T](
    report_format: ReportFormat,
    reader: Callable[[str, Path, Sequence[str]], T],
    text: str,
    root: Path,
    source_roots: Sequence[str],
) -> T:
    if len(text) > MAX_REPORT_CHARS:
        raise ReportError(
            f"a {report_format.value} report larger than {MAX_REPORT_CHARS} characters"
        )
    try:
        return reader(text, root, source_roots)
    except ReportError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
        # the file's content is not trusted: a model that refuses it is a bad report, not a bug
        raise ReportError(f"a {report_format.value} report that does not hold: {error}") from None


def read_coverage(
    report_format: ReportFormat, text: str, root: Path, source_roots: Sequence[str] = ()
) -> CoverageReport:
    reader = _COVERAGE.get(report_format)
    if reader is None:
        raise ReportError(f"{report_format.value} is not a coverage format")
    return _guarded(report_format, reader, text, root, source_roots)


def _index(text: str, _root: Path, _source_roots: Sequence[str]) -> dict[str, str]:
    return read_index(text)


def read_test_lines(
    report_format: ReportFormat,
    texts: Mapping[str, str],
    root: Path,
    source_roots: Sequence[str] = (),
    index: str | None = None,
) -> LineTests:
    """The tests of each line, from the report files in ``texts`` (the text of each, by its file
    name). Without ``index`` there is one file, in the format of the Forja. With it, the text of
    the file that names the test of each report, every file is the coverage of that one test, in
    ``report_format``; a report the index does not name belongs to no test and is not read."""
    if index is None:
        if report_format is not ReportFormat.TEST_LINES_JSON or len(texts) != 1:
            raise ReportError(f"{report_format.value}: the tests of each line come in one file")
        (text,) = texts.values()
        return _guarded(report_format, read_test_lines_json, text, root, source_roots)
    tests = _guarded(ReportFormat.TEST_LINES_JSON, _index, index, root, ())
    return tests_by_line(
        (tests[name], read_coverage(report_format, text, root, source_roots))
        for name, text in sorted(texts.items())
        if name in tests
    )
