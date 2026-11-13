"""The Java adapter: the shared promises, the commands it plans, and what it reads back.

The files in ``tests/fixtures/java`` are what the real tools printed on a small Maven project
(Maven 3.10.0, JDK 21, Surefire 3.5.2, JUnit 5.11, Spotless 2.44, Checkstyle 3.6, PMD 3.26,
SpotBugs 4.8.6, JaCoCo 0.8.13); the project's directory is ``C:/work/calc``.
"""

import shutil
from pathlib import Path

import pytest

from forja.core.adapters import (
    CommandPlan,
    FileClass,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ParseResult,
    ToolInfo,
    ToolOutput,
    Unparseable,
)
from forja.core.reports import ReportFormat, ReportSpec
from forja.core.reports.readers import read_coverage
from forja.languages.java import JavaAdapter, parsers
from forja.languages.java.adapter import COVERAGE_REPORT, JACOCO, SOURCE_ROOTS
from forja.languages.java.parsers import MAX_LINE
from tests.core.adapters.conformance import language_problems

FIXTURES = Path(__file__).parents[2] / "fixtures" / "java"
ADAPTER = JavaAdapter(maven=("m",))
WORK = Path("C:/work/calc")
BACKSLASHED = chr(92).join(("C:", "work", "calc"))
POM = "<project>{plugins}</project>"
SPOTLESS = "<artifactId>spotless-maven-plugin</artifactId>"
CHECKSTYLE = "<artifactId>maven-checkstyle-plugin</artifactId>"
PMD = "<artifactId>maven-pmd-plugin</artifactId>"
SPOTBUGS = "<artifactId>spotbugs-maven-plugin</artifactId>"


def make_project(root: Path, *plugins: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pom.xml").write_text(POM.format(plugins="".join(plugins)), encoding="utf-8")
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path / "project", SPOTLESS, CHECKSTYLE, PMD, SPOTBUGS)


def read(operation: Operation, name: str, code: int | None, root: Path = WORK) -> ParseResult:
    return ADAPTER.parse(operation, ToolOutput(code, FIXTURES / name, root))


def facts(result: object) -> list[tuple[str, str, object]]:
    assert isinstance(result, Parsed), result
    return [(d.measurement, d.target.id, d.value) for d in result.drafts]


def localize(text: str, root: Path) -> str:
    """The fixture's project directory, ``C:/work/calc`` in either spelling, made ``root``."""
    return text.replace(BACKSLASHED, str(root)).replace("C:/work/calc", root.as_posix())


# -- the shared promises ------------------------------------------------------------------------


@pytest.mark.parametrize("adapter", [JavaAdapter(), ADAPTER])
def test_the_adapter_keeps_every_promise_of_the_interface(
    adapter: JavaAdapter, project: Path, tmp_path: Path
) -> None:
    assert language_problems(adapter, project, tmp_path / "scratch") == []


# -- finding and classifying ----------------------------------------------------------------------


def test_a_project_is_found_by_its_pom(project: Path, tmp_path: Path) -> None:
    found = ADAPTER.detect(project)

    assert found is not None
    assert (found.language, found.roots, found.manifests) == ("java", (".",), ("pom.xml",))
    assert found.toolchain_probe is not None
    assert found.toolchain_probe.plan.argv == ("m", "-v")
    assert ADAPTER.detect(tmp_path / "nowhere") is None


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("src/main/java/com/acme/Calc.java", FileClass.PRODUCTION),
        ("src/test/java/com/acme/Helpers.java", FileClass.TEST),
        ("src/test/java/com/acme/CalcTest.java", FileClass.TEST),
        ("core\\src\\test\\java\\Support.java", FileClass.TEST),
        ("src/main/java/com/acme/CalcIT.java", FileClass.TEST),
        ("target/generated-sources/Gen.java", None),
        ("pom.xml", None),
        ("README.md", None),
    ],
)
def test_files_are_classified_by_how_they_are_named_and_where_they_live(
    path: str, kind: FileClass | None
) -> None:
    assert ADAPTER.classify(path) is kind


# -- commands --------------------------------------------------------------------------------------


def plan(operation: Operation, root: Path, **options: object) -> object:
    return ADAPTER.plan(operation, OperationRequest(root=root, **options))  # type: ignore[arg-type]


def argv(operation: Operation, root: Path, **options: object) -> tuple[str, ...]:
    answer = plan(operation, root, **options)
    assert isinstance(answer, CommandPlan), answer
    return answer.argv


def test_format_runs_spotless_and_rewrites_only_when_asked(project: Path) -> None:
    assert argv(Operation.FORMAT, project) == ("m", "-B", "-ntp", "spotless:apply")
    checked = argv(Operation.FORMAT, project, check_only=True)
    assert checked == ("m", "-B", "-ntp", "spotless:check")


def test_format_of_some_files_gives_spotless_a_pattern_for_their_paths(project: Path) -> None:
    command = argv(Operation.FORMAT, project, files=("src/main/java/A.java",), check_only=True)

    assert command[-1] == "-DspotlessFiles=.*[/\\\\](src[/\\\\]main[/\\\\]java[/\\\\]A\\.java)"


def test_lint_runs_the_linters_the_pom_declares_and_does_not_stop_at_the_first(
    project: Path,
) -> None:
    command = argv(Operation.LINT, project)

    assert command[:5] == ("m", "-B", "-ntp", "clean", "compile")
    assert command[5:8] == ("checkstyle:check", "pmd:check", "spotbugs:check")
    assert "-Dpmd.failOnViolation=false" in command
    only = make_project(project.parent / "only", PMD)
    assert argv(Operation.LINT, only)[5] == "pmd:check"
    assert argv(Operation.LINT, only).count("checkstyle:check") == 0


def test_a_tool_the_pom_does_not_declare_is_not_supported(tmp_path: Path) -> None:
    bare = make_project(tmp_path / "bare")

    for operation in (Operation.FORMAT, Operation.LINT, Operation.LINT_FIX):
        assert isinstance(plan(operation, bare), NotSupported)
    assert isinstance(plan(Operation.BUILD, tmp_path / "nowhere"), NotSupported)


def test_build_and_tests_start_from_a_clean_target(project: Path) -> None:
    assert argv(Operation.BUILD, project) == ("m", "-B", "-ntp", "clean", "test-compile")
    assert argv(Operation.TEST, project) == ("m", "-B", "-ntp", "clean", "test")


def test_coverage_names_the_jacoco_goals_in_full_so_the_pom_needs_nothing(project: Path) -> None:
    assert argv(Operation.COVERAGE, project) == (
        "m", "-B", "-ntp", "clean", f"{JACOCO}:prepare-agent", "test", f"{JACOCO}:report",
    )  # fmt: skip
    assert ADAPTER.report(Operation.COVERAGE) == ReportSpec(
        ReportFormat.JACOCO_XML, COVERAGE_REPORT, SOURCE_ROOTS
    )
    assert isinstance(ADAPTER.report(Operation.MUTATION), NotSupported)


def test_no_filter_of_the_tests_is_offered(project: Path) -> None:
    for operation in (Operation.TEST, Operation.COVERAGE):
        assert isinstance(plan(operation, project, tests=("adds",)), NotSupported)
        assert isinstance(plan(operation, project, test_files=("A.java",)), NotSupported)


def test_maven_may_read_where_java_and_its_plugins_are(project: Path) -> None:
    answer = plan(Operation.TEST, project)

    assert isinstance(answer, CommandPlan)
    assert {"JAVA_HOME", "USERPROFILE", "MAVEN_OPTS"} <= answer.env_allow
    assert "pom.xml" in answer.config_files


def test_every_tool_names_its_version_probe() -> None:
    info = ADAPTER.tool_info(Operation.BUILD)

    assert isinstance(info, ToolInfo)
    assert info.tool_probe.plan.argv == ("m", "-v")
    assert ADAPTER.tool_info(Operation.FORMAT).tool == "spotless"  # type: ignore[union-attr]
    assert isinstance(ADAPTER.tool_info(Operation.MUTATION), NotSupported)


# -- reading what they print ---------------------------------------------------------------------


def build_reports(tmp_path: Path, name: str) -> Path:
    root = tmp_path / "built"
    reports = root / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    shutil.copy(FIXTURES / "surefire" / name, reports / "TEST-com.acme.CalcTest.xml")
    return root


def test_the_outcome_of_every_test_case_is_read_from_the_surefire_report(tmp_path: Path) -> None:
    root = build_reports(tmp_path, "TEST-pass.xml")

    result = ADAPTER.parse(Operation.TEST, ToolOutput(0, FIXTURES / "test-pass.txt", root))

    assert facts(result) == [
        ("test.outcome", "com.acme.CalcTest.addsMany(int, int, int)[1]", "passed"),
        ("test.outcome", "com.acme.CalcTest.addsMany(int, int, int)[2]", "passed"),
        ("test.outcome", "com.acme.CalcTest.addsTwoNumbers", "passed"),
        ("test.outcome", "com.acme.CalcTest.later", "skipped"),
        ("test.skipped", "tests", 1),
    ]


def test_a_failed_test_case_is_a_failed_outcome(tmp_path: Path) -> None:
    root = build_reports(tmp_path, "TEST-fail.xml")

    result = ADAPTER.parse(Operation.TEST, ToolOutput(1, FIXTURES / "test-fail.txt", root))

    assert ("test.outcome", "com.acme.CalcTest.addsTwoNumbers", "failed") in facts(result)


def test_test_output_that_contradicts_the_exit_code_is_not_understood(tmp_path: Path) -> None:
    passing = build_reports(tmp_path / "a", "TEST-pass.xml")
    failing = build_reports(tmp_path / "b", "TEST-fail.xml")

    def run(root: Path, code: int | None) -> ParseResult:
        return ADAPTER.parse(Operation.TEST, ToolOutput(code, FIXTURES / "test-pass.txt", root))

    assert isinstance(run(failing, 0), Unparseable)
    assert isinstance(run(passing, 1), Unparseable)
    assert isinstance(run(passing, 2), Unparseable)
    assert isinstance(run(passing, None), Unparseable)
    assert isinstance(run(tmp_path / "none", 1), Unparseable)


def test_a_project_without_test_reports_collects_none(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    result = ADAPTER.parse(Operation.TEST, ToolOutput(0, FIXTURES / "test-pass.txt", empty))

    assert facts(result) == [("test.collected", "tests", 0)]


def test_build_errors_are_reported_per_file_relative_to_the_project() -> None:
    assert facts(read(Operation.BUILD, "build-error.txt", 1)) == [
        ("build.compile_error", "src/main/java/com/acme/Bad.java", 1)
    ]
    assert facts(read(Operation.BUILD, "build-error.txt", 0)) == []
    assert isinstance(read(Operation.BUILD, "build-error.txt", None), Unparseable)


def test_a_build_failure_without_a_file_is_one_failure_of_the_build(tmp_path: Path) -> None:
    log = tmp_path / "b.log"
    log.write_text("[ERROR] Failed to execute goal on project calc\n", encoding="utf-8")

    assert facts(ADAPTER.parse(Operation.BUILD, ToolOutput(1, log, WORK))) == [
        ("build.failed", "build", 1)
    ]


def test_spotless_lists_the_files_that_are_not_in_googles_form() -> None:
    assert facts(read(Operation.FORMAT, "format.txt", 1)) == [
        ("format.unformatted", "src/main/java/com/acme/Ugly.java", 1)
    ]
    assert isinstance(read(Operation.FORMAT, "format.txt", 2), Unparseable)


def test_lint_counts_checkstyle_from_the_log_and_pmd_and_spotbugs_from_their_reports(
    tmp_path: Path,
) -> None:
    root = tmp_path / "built"
    source = root / "src" / "main" / "java" / "com" / "acme"
    source.mkdir(parents=True)
    (source / "Ugly.java").write_text("class Ugly {}", encoding="utf-8")
    (root / "target").mkdir()
    for fixture, name in (("pmd.xml", "pmd.xml"), ("spotbugs.xml", "spotbugsXml.xml")):
        text = (FIXTURES / fixture).read_text(encoding="utf-8")
        (root / "target" / name).write_text(localize(text, root), encoding="utf-8")
    log = tmp_path / "lint.log"
    text = (FIXTURES / "lint.txt").read_text(encoding="utf-8")
    log.write_text(localize(text, root), encoding="utf-8")

    result = facts(ADAPTER.parse(Operation.LINT, ToolOutput(0, log, root)))

    ugly = "src/main/java/com/acme/Ugly.java"
    assert ("lint.pmd.UnusedLocalVariable", ugly, 1) in result
    assert ("lint.spotbugs.NP_ALWAYS_NULL", ugly, 1) in result
    assert ("lint.checkstyle.ParenPad", ugly, 2) in result


def test_lint_output_that_failed_without_a_finding_is_not_understood(tmp_path: Path) -> None:
    log = tmp_path / "l.log"
    log.write_text("[ERROR] Could not resolve dependencies\n", encoding="utf-8")

    assert isinstance(ADAPTER.parse(Operation.LINT, ToolOutput(1, log, tmp_path)), Unparseable)
    assert facts(ADAPTER.parse(Operation.LINT, ToolOutput(0, log, tmp_path))) == []


def test_the_real_jacoco_report_names_the_lines_the_tests_ran(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main" / "java" / "com" / "acme"
    source.mkdir(parents=True)
    (source / "Calc.java").write_text("package com.acme;\n", encoding="utf-8")
    text = (FIXTURES / "jacoco.xml").read_text(encoding="utf-8")

    report = read_coverage(ReportFormat.JACOCO_XML, text, tmp_path, SOURCE_ROOTS)

    calc = report.of("src/main/java/com/acme/Calc.java")
    assert calc is not None
    assert 7 in calc.covered
    assert not calc.missed


def test_through_cmd_exe_only_one_file_can_be_asked_for(tmp_path: Path) -> None:
    root = make_project(tmp_path / "cmd", SPOTLESS)
    launcher = JavaAdapter(maven=("C:/tools/mvn.cmd",))
    several = OperationRequest(root=root, files=("a.java", "b.java"), check_only=True)
    single = OperationRequest(root=root, files=("a.java",), check_only=True)

    assert isinstance(launcher.plan(Operation.FORMAT, several), NotSupported)
    assert isinstance(launcher.plan(Operation.FORMAT, single), CommandPlan)
    assert isinstance(ADAPTER.plan(Operation.FORMAT, several), CommandPlan)


def test_spotless_failing_without_naming_a_file_is_not_understood(tmp_path: Path) -> None:
    log = tmp_path / "f.log"
    log.write_text("[ERROR] Could not resolve dependencies\n", encoding="utf-8")

    assert isinstance(ADAPTER.parse(Operation.FORMAT, ToolOutput(1, log, WORK)), Unparseable)
    assert facts(ADAPTER.parse(Operation.FORMAT, ToolOutput(0, log, WORK))) == []


@pytest.mark.parametrize("outside", ["/elsewhere/dir/X.java", "D:/elsewhere/dir/X.java"])
def test_a_path_outside_the_project_is_named_by_its_file_only(outside: str, tmp_path: Path) -> None:
    log = tmp_path / "f.log"
    log.write_text(f"[ERROR]     {outside}\n", encoding="utf-8")

    assert facts(ADAPTER.parse(Operation.FORMAT, ToolOutput(1, log, WORK))) == [
        ("format.unformatted", "X.java", 1)
    ]


def test_a_line_of_exactly_the_limit_is_still_read_and_a_longer_one_is_not(
    tmp_path: Path,
) -> None:
    def unformatted(name: str) -> ParseResult:
        log = tmp_path / "f.log"
        log.write_text(f"[ERROR]     {name}.java\n", encoding="utf-8")
        return ADAPTER.parse(Operation.FORMAT, ToolOutput(1, log, WORK))

    longest = "U" * (MAX_LINE - len("[ERROR]     .java"))

    assert facts(unformatted(longest)) == [("format.unformatted", f"{longest}.java", 1)]
    assert isinstance(unformatted(longest + "U"), Unparseable)


def test_a_directory_named_like_a_report_is_not_read_as_one(tmp_path: Path) -> None:
    root = build_reports(tmp_path, "TEST-pass.xml")
    (root / "target" / "surefire-reports" / "TEST-a-directory.xml").mkdir()

    result = ADAPTER.parse(Operation.TEST, ToolOutput(0, FIXTURES / "test-pass.txt", root))

    assert ("test.outcome", "com.acme.CalcTest.addsTwoNumbers", "passed") in facts(result)


def test_a_report_of_exactly_the_limit_is_read_and_a_larger_one_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = build_reports(tmp_path, "TEST-pass.xml")
    size = (root / "target" / "surefire-reports" / "TEST-com.acme.CalcTest.xml").stat().st_size

    def read_with(limit: int) -> list[tuple[str, str, object]]:
        monkeypatch.setattr(parsers, "MAX_REPORT", limit)
        return facts(ADAPTER.parse(Operation.TEST, ToolOutput(0, FIXTURES / "test-pass.txt", root)))

    assert ("test.outcome", "com.acme.CalcTest.addsTwoNumbers", "passed") in read_with(size)
    assert read_with(size - 1) == [("test.collected", "tests", 0)]
