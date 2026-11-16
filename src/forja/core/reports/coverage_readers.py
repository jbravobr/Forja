"""Readers of the native coverage reports, one per format. Each answers a ``CoverageReport``.

Where a format cannot say more than it says (Go reports blocks of lines, not lines; Istanbul
reports statements), the reader takes the reading that never invents coverage: a line is covered
only if something that starts or spans there ran.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from forja.core.reports.models import CoverageReport, FileCoverage
from forja.core.reports.reading import (
    MAX_LINE,
    ReportError,
    load_json,
    load_xml,
    number,
    resolve_path,
)


class _Lines:
    """Hits per line of one file, merged over however many records the tool wrote for it."""

    def __init__(self) -> None:
        self.hit: set[int] = set()
        self.seen: set[int] = set()
        self.tests: dict[int, set[str]] = defaultdict(set)

    def add(self, line: int, hits: int) -> None:
        if not 1 <= line <= MAX_LINE:
            raise ReportError(f"a line number out of range: {line}")
        self.seen.add(line)
        if hits > 0:
            self.hit.add(line)

    def build(self) -> FileCoverage:
        return FileCoverage(
            covered=frozenset(self.hit),
            missed=frozenset(self.seen - self.hit),
            tests={
                line: frozenset(names) for line, names in self.tests.items() if line in self.hit
            },
        )


def _report(files: dict[str, _Lines]) -> CoverageReport:
    return CoverageReport({path: lines.build() for path, lines in sorted(files.items())})


_PHASE = re.compile(r"\|(setup|run|call|teardown)$")


def _test_of_context(context: object) -> str:
    """The test a coverage.py context names: ``tests/test_a.py::test_x``, or with the phase
    pytest-cov adds, ``tests/test_a.py::test_x|run``. ``''`` is no test. Only that ending is
    cut: the id of a parametrized test may hold a ``|`` of its own."""
    return _PHASE.sub("", str(context))


def read_coverage_py_json(
    text: str, root: Path, source_roots: Sequence[str] = ()
) -> CoverageReport:
    """``coverage json``; with ``--show-contexts`` it also says which tests ran each line."""
    document = load_json(text)
    files = document.get("files") if isinstance(document, dict) else None
    if not isinstance(files, dict):
        raise ReportError("coverage.py JSON without a 'files' object")
    result: dict[str, _Lines] = {}
    for raw, data in files.items():
        if not isinstance(data, dict):
            raise ReportError(f"coverage.py JSON: {raw!r} is not an object")
        lines = result.setdefault(resolve_path(raw, root, source_roots), _Lines())
        for key, hits in (("executed_lines", 1), ("missing_lines", 0)):
            for line in data.get(key, []):
                lines.add(number(line, key), hits)
        contexts = data.get("contexts", {})
        if isinstance(contexts, dict):
            for line, names in contexts.items():
                found = {test for test in map(_test_of_context, names) if test}
                if found:
                    lines.tests[number(line, "context line")] |= found
    return _report(result)


def read_jacoco_xml(text: str, root: Path, source_roots: Sequence[str] = ()) -> CoverageReport:
    """JaCoCo: ``<package name="a/b"><sourcefile name="C.java"><line nr mi ci .../>``."""
    tree = load_xml(text)
    if tree.tag != "report":
        raise ReportError("JaCoCo XML must start with <report>")
    result: dict[str, _Lines] = {}
    for package in tree.iter("package"):
        for source in package.iter("sourcefile"):
            name = f"{package.get('name', '')}/{source.get('name', '')}".strip("/")
            lines = result.setdefault(resolve_path(name, root, source_roots), _Lines())
            for line in source.iter("line"):
                covered = number(line.get("ci", "0"), "ci")
                missed = number(line.get("mi", "0"), "mi")
                if covered or missed:  # a line with neither is not code
                    lines.add(number(line.get("nr"), "nr"), covered)
    return _report(result)


def read_cobertura_xml(text: str, root: Path, source_roots: Sequence[str] = ()) -> CoverageReport:
    """Cobertura, as written by coverlet: ``<class filename><lines><line number hits/>``."""
    tree = load_xml(text)
    if tree.tag != "coverage":
        raise ReportError("Cobertura XML must start with <coverage>")
    sources = [s.text.strip() for s in tree.iter("source") if s.text and s.text.strip()]
    result: dict[str, _Lines] = {}
    for cls in tree.iter("class"):
        filename = cls.get("filename")
        if not filename:
            raise ReportError("Cobertura XML: a class without a filename")
        path = resolve_path(filename, root, source_roots)
        if not (root / path).is_file():  # a relative filename: it is relative to a <source>
            for source in sources:
                joined = resolve_path(f"{source}/{filename}", root, source_roots)
                if (root / joined).is_file():
                    path = joined
                    break
        lines = result.setdefault(path, _Lines())
        # a class lists its lines directly and again under each method: the direct list is whole
        direct = cls.find("lines")
        if direct is None:
            continue
        for line in direct.iter("line"):
            lines.add(number(line.get("number"), "number"), number(line.get("hits", "0"), "hits"))
    return _report(result)


def read_istanbul_json(text: str, root: Path, source_roots: Sequence[str] = ()) -> CoverageReport:
    """``coverage-final.json`` (istanbul, c8): statements by start line, with a hit count each."""
    document = load_json(text)
    if not isinstance(document, dict):
        raise ReportError("Istanbul JSON must be an object of files")
    result: dict[str, _Lines] = {}
    for raw, data in document.items():
        if not isinstance(data, dict) or "statementMap" not in data or "s" not in data:
            raise ReportError(f"Istanbul JSON: {raw!r} has no statementMap and counts")
        lines = result.setdefault(
            resolve_path(str(data.get("path", raw)), root, source_roots), _Lines()
        )
        for key, where in data["statementMap"].items():
            try:
                start = number(where["start"]["line"], "statement line")
            except (KeyError, TypeError):
                raise ReportError(f"Istanbul JSON: statement {key} without a start line") from None
            lines.add(start, number(data["s"].get(key, 0), "statement count"))
    return _report(result)


def read_go_coverprofile(text: str, root: Path, source_roots: Sequence[str] = ()) -> CoverageReport:
    """``go test -coverprofile``: ``file:l.c,l.c statements count`` per block.

    A block covers every line from its first to its last. A line shared by blocks is covered if
    any block that holds it ran.
    """
    rows = [row for row in text.splitlines() if row.strip()]
    if not rows or not rows[0].startswith("mode:"):
        raise ReportError("a Go cover profile starts with a 'mode:' line")
    result: dict[str, _Lines] = {}
    for row in rows[1:]:
        try:
            where, _statements, count = row.rsplit(" ", 2)
            name, span = where.rsplit(":", 1)
            begin, end = span.split(",")
            first, last = int(begin.split(".")[0]), int(end.split(".")[0])
            hits = int(count)
        except ValueError:
            raise ReportError(f"a Go cover profile row that cannot be read: {row!r}") from None
        if first > last:
            raise ReportError(f"a Go cover profile block that ends before it starts: {row!r}")
        if last > MAX_LINE:  # checked before the loop: a block is a range, not a list
            raise ReportError(f"a Go cover profile block with a line out of range: {row!r}")
        lines = result.setdefault(resolve_path(name, root, source_roots), _Lines())
        for line in range(first, last + 1):
            lines.add(line, hits)
    return _report(result)
