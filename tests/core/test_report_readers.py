"""Each native report format reads into the common shape. The fixtures are in
``tests/fixtures/reports``: coverage.py's is a real recording; the others are written by hand
from the tools' documented formats and must be re-recorded when their toolchains come in."""

import random
from pathlib import Path

import pytest

from forja.core.reports import (
    COVERAGE_FORMATS,
    MutantStatus,
    ReportFormat,
)
from forja.core.reports.readers import read_coverage
from forja.core.reports.reading import ReportError, resolve_path

FIXTURES = Path(__file__).parent.parent / "fixtures" / "reports"
F = ReportFormat


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def project(tmp_path: Path, *files: str) -> Path:
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n")
    return tmp_path


def summary(report: object) -> list[tuple[str, int, MutantStatus]]:
    return [(m.file, m.line, m.status) for m in report.mutants]  # type: ignore[attr-defined]


# -- coverage ------------------------------------------------------------------------------------


def test_coverage_py_says_which_test_ran_each_line(tmp_path: Path) -> None:
    root = project(tmp_path, "m.py", "test_m.py")
    report = read_coverage(F.COVERAGE_PY_JSON, fixture("coverage-py.json"), root)
    m = report.of("m.py")
    assert m is not None
    assert (m.covered, m.missed) == (frozenset({1, 2, 3}), frozenset({4}))
    assert m.tests == {2: frozenset({"test_m.test_f"}), 3: frozenset({"test_m.test_f"})}


def test_jacoco_reads_lines_through_the_package_and_the_source_root(tmp_path: Path) -> None:
    root = project(tmp_path, "src/main/java/com/acme/Calc.java")
    report = read_coverage(F.JACOCO_XML, fixture("jacoco.xml"), root, ("src/main/java",))
    calc = report.of("src/main/java/com/acme/Calc.java")
    assert calc is not None
    assert (calc.covered, calc.missed) == (frozenset({3, 4}), frozenset({5}))


def test_cobertura_merges_the_classes_of_one_file_and_ignores_the_method_copies(
    tmp_path: Path,
) -> None:
    root = project(tmp_path, "src/Math.cs")
    report = read_coverage(F.COBERTURA_XML, fixture("cobertura.xml"), root)
    math = report.of("src/Math.cs")
    assert math is not None
    assert (math.covered, math.missed) == (frozenset({7, 12}), frozenset({8}))


def test_istanbul_counts_statements_by_their_first_line(tmp_path: Path) -> None:
    root = project(tmp_path, "src/calc.js")
    report = read_coverage(F.ISTANBUL_JSON, fixture("istanbul.json"), root)
    calc = report.of("src/calc.js")
    assert calc is not None
    assert (calc.covered, calc.missed) == (frozenset({1, 2}), frozenset({4}))


def test_go_covers_every_line_of_a_block_that_ran(tmp_path: Path) -> None:
    root = project(tmp_path, "calc.go")
    report = read_coverage(F.GO_COVERPROFILE, fixture("gocover.out"), root)
    calc = report.of("calc.go")
    assert calc is not None
    assert calc.covered == frozenset({3, 4, 5, 8, 9, 10})
    assert calc.missed == frozenset({7})  # line 8 is in both a missed and a hit block: hit wins


@pytest.mark.parametrize(
    ("report_format", "text"),
    [
        (F.COVERAGE_PY_JSON, '{"files": 3}'),
        (F.JACOCO_XML, "<coverage/>"),
        (F.COBERTURA_XML, "<report/>"),
        (F.ISTANBUL_JSON, '{"a.js": {"path": "a.js"}}'),
        (F.GO_COVERPROFILE, "a.go:1.1,2.2 1 1"),
    ],
)
def test_a_coverage_report_of_the_wrong_shape_is_a_report_error(
    tmp_path: Path, report_format: ReportFormat, text: str
) -> None:
    with pytest.raises(ReportError):
        read_coverage(report_format, text, tmp_path)


# -- what every reader shares --------------------------------------------------------------------


def test_xml_with_entities_is_refused_before_it_is_parsed(tmp_path: Path) -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE report [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        "<report>&b;</report>"
    )
    with pytest.raises(ReportError, match="entities"):
        read_coverage(F.JACOCO_XML, bomb, tmp_path)


def test_no_reader_fails_with_anything_but_a_report_error(tmp_path: Path) -> None:
    rng = random.Random(7)
    seeds = [fixture(name) for name in sorted(p.name for p in FIXTURES.iterdir())]
    samples = ["", "\x00", "{", "[]", "null", "<", "<a/>", "mode: set", *seeds]
    for seed in seeds:  # damaged copies of real reports
        for _ in range(30):
            cut = rng.randrange(len(seed))
            samples.append(seed[:cut] + seed[cut + rng.randrange(1, 9) :])
    refused = 0
    for sample in samples:
        for report_format in COVERAGE_FORMATS:
            try:
                read_coverage(report_format, sample, tmp_path)
            except ReportError:
                refused += 1

    assert refused >= len(samples)  # no sample is a report in every format


# -- paths ---------------------------------------------------------------------------------------


def test_an_absolute_path_inside_the_repository_becomes_relative(tmp_path: Path) -> None:
    root = project(tmp_path, "src/a.py")
    assert resolve_path(str(root / "src" / "a.py"), root) == "src/a.py"


def test_a_module_path_is_cut_until_it_names_a_file(tmp_path: Path) -> None:
    root = project(tmp_path, "pkg/a.go")
    assert resolve_path("example.com/calc/pkg/a.go", root) == "pkg/a.go"


def test_a_file_that_exists_nowhere_keeps_its_plain_reading(tmp_path: Path) -> None:
    assert resolve_path("a\\b\\c.py", tmp_path) == "a/b/c.py"


def test_a_path_that_leaves_the_repository_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="leaves the repository"):
        resolve_path("../x.py", tmp_path)
    with pytest.raises(ReportError, match="empty"):
        resolve_path("  ", tmp_path)


def test_a_network_or_device_path_is_refused_and_never_opened(tmp_path: Path) -> None:
    for bad in (
        "//server/share/a.py",
        "\\\\server\\share\\a.py",
        "\\\\?\\C:\\a.py",
        "//./pipe/x",
    ):
        with pytest.raises(ReportError, match="network or device"):
            resolve_path(bad, tmp_path)
    with pytest.raises(ReportError, match="leaves the repository"):
        resolve_path("a/b:stream", tmp_path)


def test_an_absolute_path_outside_the_repository_stays_outside(tmp_path: Path) -> None:
    root = project(tmp_path / "repo", "src/a.py")
    assert resolve_path(str(tmp_path / "other" / "a.py"), root) != "src/a.py"
    with pytest.raises(ReportError, match="leaves the repository"):
        resolve_path(str(root) + "/../other.py", root)


def test_a_block_that_claims_absurdly_many_lines_is_refused_without_looping(
    tmp_path: Path,
) -> None:
    with pytest.raises(ReportError, match="out of range"):
        read_coverage(F.GO_COVERPROFILE, "mode: set\na.go:1.1,999999999.2 1 1\n", tmp_path)
    with pytest.raises(ReportError, match="out of range"):
        read_coverage(
            F.COVERAGE_PY_JSON,
            '{"files": {"a.py": {"executed_lines": [99999999999]}}}',
            tmp_path,
        )


def test_a_report_larger_than_the_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("forja.core.reports.readers.MAX_REPORT_CHARS", 10)
    with pytest.raises(ReportError, match="larger than"):
        read_coverage(F.GO_COVERPROFILE, "mode: set\na.go:1.1,2.2 1 1\n", tmp_path)
