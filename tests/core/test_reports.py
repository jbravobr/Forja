import pytest

from forja.core.adapters import Operation
from forja.core.reports import (
    COVERAGE_FORMATS,
    TEST_LINES_DIRECTORY,
    TEST_LINES_FORMATS,
    CoverageReport,
    FileCoverage,
    MutantResult,
    MutantStatus,
    MutationReport,
    ReportFormat,
    ReportSpec,
    formats_of,
)


def mutant(file: str, line: int, status: MutantStatus, change: str = "< -> <=") -> MutantResult:
    return MutantResult(file, line, change, status)


# -- FileCoverage --------------------------------------------------------------------------------


def test_fraction_counts_only_executable_lines() -> None:
    file = FileCoverage(covered=frozenset({1, 2, 3}), missed=frozenset({4}))
    assert file.fraction() == 0.75
    assert file.fraction([3, 4, 99]) == 0.5  # 99 is not executable: it does not count


def test_fraction_is_none_when_nothing_is_executable() -> None:
    file = FileCoverage(covered=frozenset({1}), missed=frozenset({2}))
    assert file.fraction([10, 11]) is None
    assert FileCoverage(covered=frozenset()).fraction() is None


def test_a_line_cannot_be_covered_and_missed() -> None:
    with pytest.raises(ValueError, match="both covered and missed"):
        FileCoverage(covered=frozenset({1}), missed=frozenset({1}))


def test_only_covered_lines_have_tests() -> None:
    with pytest.raises(ValueError, match="only covered lines"):
        FileCoverage(covered=frozenset({1}), missed=frozenset({2}), tests={2: frozenset({"t"})})
    FileCoverage(covered=frozenset({1}), tests={1: frozenset({"t"})})


def test_lines_start_at_one() -> None:
    with pytest.raises(ValueError, match="start at 1"):
        FileCoverage(covered=frozenset({0}))


# -- CoverageReport ------------------------------------------------------------------------------


def test_an_unseen_file_is_not_the_same_as_an_uncovered_one() -> None:
    report = CoverageReport({"a.py": FileCoverage(covered=frozenset(), missed=frozenset({1}))})
    assert report.of("a.py") is not None
    assert report.of("b.py") is None
    assert not report.is_covered("a.py", 1)
    assert not report.is_covered("b.py", 1)


def test_report_paths_must_be_relative_posix() -> None:
    for bad in ("C:/x.py", "/x.py", "..\\x.py", "a/../x.py"):
        with pytest.raises(ValueError, match="relative POSIX"):
            CoverageReport({bad: FileCoverage(covered=frozenset())})


# -- MutationReport ------------------------------------------------------------------------------


def test_on_lines_keeps_only_the_changed_lines_of_the_changed_files() -> None:
    report = MutationReport(
        (
            mutant("a.py", 1, MutantStatus.SURVIVED),
            mutant("a.py", 2, MutantStatus.KILLED),
            mutant("b.py", 1, MutantStatus.SURVIVED),
        )
    )
    kept = report.on_lines({"a.py": frozenset({1})})
    assert kept.mutants == (report.mutants[0],)


def test_with_status_picks_one_status() -> None:
    report = MutationReport(
        (mutant("a.py", 1, MutantStatus.SURVIVED), mutant("a.py", 2, MutantStatus.KILLED))
    )
    assert [m.line for m in report.with_status(MutantStatus.SURVIVED)] == [1]


def test_score_counts_timeouts_as_killed_and_leaves_uncovered_out() -> None:
    report = MutationReport(
        (
            mutant("a.py", 1, MutantStatus.KILLED),
            mutant("a.py", 2, MutantStatus.TIMEOUT),
            mutant("a.py", 3, MutantStatus.SURVIVED),
            mutant("a.py", 4, MutantStatus.UNCOVERED),
        )
    )
    assert report.score == 2 / 3
    assert MutationReport(()).score is None
    assert MutationReport((mutant("a.py", 4, MutantStatus.UNCOVERED),)).score is None


def test_a_mutant_needs_its_change_text_and_a_valid_place() -> None:
    with pytest.raises(ValueError, match="text of its change"):
        MutantResult("a.py", 1, "", MutantStatus.KILLED)
    with pytest.raises(ValueError, match="start at 1"):
        MutantResult("a.py", 0, "x", MutantStatus.KILLED)
    with pytest.raises(ValueError, match="relative POSIX"):
        MutantResult("/a.py", 1, "x", MutantStatus.KILLED)


# -- report formats ------------------------------------------------------------------------------


def test_every_format_but_the_one_of_the_forja_is_a_coverage_format() -> None:
    assert set(ReportFormat) - {ReportFormat.TEST_LINES_JSON} == COVERAGE_FORMATS
    assert formats_of(Operation.COVERAGE) == COVERAGE_FORMATS
    # the tests of each line: the file that says it, or a coverage report for each test
    assert formats_of(Operation.TEST_LINES) == COVERAGE_FORMATS | {ReportFormat.TEST_LINES_JSON}
    assert formats_of(Operation.TEST_LINES) == TEST_LINES_FORMATS
    assert formats_of(Operation.MUTATION) == frozenset()
    assert formats_of(Operation.TEST) == frozenset()


def test_a_report_path_must_be_relative_posix() -> None:
    ReportSpec(ReportFormat.JACOCO_XML, "target/site/jacoco/jacoco.xml")
    for bad in ("/abs.xml", "..\\x.xml", "a/../x.xml", "C:/x.xml"):
        with pytest.raises(ValueError, match="relative POSIX"):
            ReportSpec(ReportFormat.JACOCO_XML, bad)


def test_the_index_of_a_report_for_each_test_must_be_relative_posix_too() -> None:
    directory = TEST_LINES_DIRECTORY
    assert directory == ".forja-test-lines"
    spec = ReportSpec(ReportFormat.GO_COVERPROFILE, f"{directory}/*.out", index=f"{directory}/i")
    assert spec.index == ".forja-test-lines/i"
    assert ReportSpec(ReportFormat.JACOCO_XML, "a.xml").index is None
    with pytest.raises(ValueError, match="relative POSIX"):
        ReportSpec(ReportFormat.GO_COVERPROFILE, f"{directory}/*.out", index="/abs/index.json")


# -- the tests of each line ------------------------------------------------------------------


def _named() -> CoverageReport:
    return CoverageReport(
        {
            "a.py": FileCoverage(
                covered=frozenset({1, 2, 3}),
                missed=frozenset({4}),
                tests={1: frozenset({"t1"}), 2: frozenset({"t1", "t2"})},
            ),
            "b.py": FileCoverage(covered=frozenset({1})),
        }
    )


def test_a_report_names_tests_when_any_line_of_any_file_has_them() -> None:
    assert _named().names_tests is True
    assert CoverageReport({"b.py": FileCoverage(covered=frozenset({1}))}).names_tests is False
    assert CoverageReport({}).names_tests is False


def test_the_tests_of_some_lines_are_those_of_any_of_them() -> None:
    report = _named()

    assert report.tests_of("a.py", (1,)) == frozenset({"t1"})
    assert report.tests_of("a.py", (1, 2)) == frozenset({"t1", "t2"})
    assert report.tests_of("a.py", (3, 4, 99)) == frozenset()  # run, but by no test it names
    assert report.tests_of("a.py", ()) == frozenset()
    assert report.tests_of("b.py", (1,)) == frozenset()
    assert report.tests_of("unseen.py", (1,)) == frozenset()


def test_tests_found_apart_are_added_to_the_covered_lines_and_to_no_other() -> None:
    found = {
        "a.py": {2: frozenset({"t3"}), 3: frozenset({"t4"}), 4: frozenset({"t5"})},
        "unseen.py": {1: frozenset({"t6"})},
    }

    report = _named().with_tests(found)

    a = report.of("a.py")
    assert a is not None
    assert a.tests == {
        1: frozenset({"t1"}),
        2: frozenset({"t1", "t2", "t3"}),
        3: frozenset({"t4"}),
    }  # line 4 is not covered in the report, whatever a test alone ran
    assert (a.covered, a.missed) == (frozenset({1, 2, 3}), frozenset({4}))
    assert report.of("b.py") == _named().of("b.py")
    assert report.of("unseen.py") is None  # the report is what says which files there are


def test_adding_no_tests_leaves_the_report_as_it_was() -> None:
    assert _named().with_tests({}) == _named()
