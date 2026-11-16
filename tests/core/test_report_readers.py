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
from forja.core.reports.readers import read_coverage, read_test_lines
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


def test_the_test_of_a_context_keeps_a_bar_of_its_own_and_loses_only_the_phase(
    tmp_path: Path,
) -> None:
    root = project(tmp_path, "a.py")
    contexts = [
        "tests/test_a.py::test_x[a|b]",
        "tests/test_a.py::test_y|run",
        "tests/test_a.py::test_z[run|setup]|teardown",
        "",
    ]
    text = (
        '{"files": {"a.py": {"executed_lines": [1, 2], "missing_lines": [],'
        f' "contexts": {{"1": {contexts!r}, "2": [""]}}}}}}}}'
    ).replace("'", '"')

    report = read_coverage(F.COVERAGE_PY_JSON, text, root)

    a = report.of("a.py")
    assert a is not None
    assert a.tests == {
        1: frozenset(
            {
                "tests/test_a.py::test_x[a|b]",
                "tests/test_a.py::test_y",
                "tests/test_a.py::test_z[run|setup]",
            }
        )
    }  # line 2 was run by no test


# -- the tests of each line ----------------------------------------------------------------------


def test_the_file_of_the_forja_gives_the_tests_of_each_line(tmp_path: Path) -> None:
    root = project(tmp_path, "src/a.go", "src/b.go")
    text = (
        '{"src/a.go": {"3": ["p/TestA", "p/TestB/sub"], "4": ["p/TestA"]},'
        f' "{(root / "src" / "b.go").as_posix()}": {{"9": ["q/TestC"]}}}}'
    )

    found = read_test_lines(F.TEST_LINES_JSON, {"lines.json": text}, root)

    assert found == {
        "src/a.go": {3: frozenset({"p/TestA", "p/TestB/sub"}), 4: frozenset({"p/TestA"})},
        "src/b.go": {9: frozenset({"q/TestC"})},  # an absolute path becomes relative
    }


def test_a_file_named_twice_in_the_file_of_the_forja_adds_up(tmp_path: Path) -> None:
    root = project(tmp_path, "src/main/java/p/A.java")
    text = '{"p/A.java": {"3": ["t1"]}, "src/main/java/p/A.java": {"3": ["t2"], "5": ["t2"]}}'

    found = read_test_lines(F.TEST_LINES_JSON, {"x.json": text}, root, ("src/main/java",))

    assert found == {"src/main/java/p/A.java": {3: frozenset({"t1", "t2"}), 5: frozenset({"t2"})}}


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        '{"a.go": []}',
        '{"a.go": {"x": ["t"]}}',
        '{"a.go": {"0": ["t"]}}',
        '{"a.go": {"99999999999": ["t"]}}',
        '{"a.go": {"1": "t"}}',
        '{"a.go": {"1": [3]}}',
        '{"a.go": {"1": [""]}}',
        '{"../a.go": {"1": ["t"]}}',
        "not json",
    ],
)
def test_a_file_of_the_forja_of_the_wrong_shape_is_a_report_error(
    tmp_path: Path, text: str
) -> None:
    with pytest.raises(ReportError):
        read_test_lines(F.TEST_LINES_JSON, {"x.json": text}, tmp_path)


def test_without_an_index_the_tests_of_each_line_come_in_one_file_of_the_forja(
    tmp_path: Path,
) -> None:
    with pytest.raises(ReportError, match="one file"):
        read_test_lines(F.TEST_LINES_JSON, {"a.json": "{}", "b.json": "{}"}, tmp_path)
    with pytest.raises(ReportError, match="one file"):
        read_test_lines(F.TEST_LINES_JSON, {}, tmp_path)
    with pytest.raises(ReportError, match="go_coverprofile"):
        read_test_lines(F.GO_COVERPROFILE, {"a.out": "mode: set\n"}, tmp_path)
    assert read_test_lines(F.TEST_LINES_JSON, {"a.json": "{}"}, tmp_path) == {}


def test_a_coverage_report_for_each_test_gives_the_tests_of_each_line(tmp_path: Path) -> None:
    root = project(tmp_path, "calc.go", "other.go")
    texts = {
        "0.out": "mode: set\nm/calc.go:3.1,4.2 1 1\nm/calc.go:7.1,7.9 1 0\n",
        "1.out": "mode: set\nm/calc.go:4.1,4.9 1 1\nm/other.go:2.1,2.9 1 1\n",
        "2.out": "mode: set\nm/calc.go:9.1,9.9 1 1\n",  # of no test: the index does not name it
    }
    index = '{"0.out": "m/TestA", "1.out": "m/TestB/sub one"}'

    found = read_test_lines(F.GO_COVERPROFILE, texts, root, index=index)

    assert found == {
        "calc.go": {3: frozenset({"m/TestA"}), 4: frozenset({"m/TestA", "m/TestB/sub one"})},
        "other.go": {2: frozenset({"m/TestB/sub one"})},
    }  # line 7 was not run by the test, and line 9 has no test


def test_two_reports_of_the_same_test_add_up(tmp_path: Path) -> None:
    root = project(tmp_path, "calc.go")
    texts = {
        "a.out": "mode: set\nm/calc.go:3.1,3.2 1 1\n",
        "b.out": "mode: set\nm/calc.go:5.1,5.2 1 1\n",
    }

    found = read_test_lines(F.GO_COVERPROFILE, texts, root, index='{"a.out": "T", "b.out": "T"}')

    assert found == {"calc.go": {3: frozenset({"T"}), 5: frozenset({"T"})}}


@pytest.mark.parametrize("index", ["[]", '{"0.out": 3}', '{"0.out": ""}', "not json"])
def test_an_index_of_the_wrong_shape_is_a_report_error(tmp_path: Path, index: str) -> None:
    with pytest.raises(ReportError):
        read_test_lines(F.GO_COVERPROFILE, {"0.out": "mode: set\n"}, tmp_path, index=index)


def test_a_report_of_a_test_that_cannot_be_read_is_a_report_error(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="mode"):
        read_test_lines(F.GO_COVERPROFILE, {"0.out": "garbage"}, tmp_path, index='{"0.out": "T"}')
    with pytest.raises(ReportError, match="not a coverage format"):
        read_test_lines(F.TEST_LINES_JSON, {"0.out": "{}"}, tmp_path, index='{"0.out": "T"}')


def test_an_index_larger_than_the_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("forja.core.reports.readers.MAX_REPORT_CHARS", 10)

    with pytest.raises(ReportError, match="larger than"):
        read_test_lines(
            F.GO_COVERPROFILE, {"0.out": "mode: set\n"}, tmp_path, index='{"0.out": "TestA"}'
        )
    with pytest.raises(ReportError, match="larger than"):
        read_test_lines(F.TEST_LINES_JSON, {"x.json": '{"a.go": {"1": ["t"]}}'}, tmp_path)
