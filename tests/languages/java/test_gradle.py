"""The Java adapter on a Gradle project: what it detects and the commands it plans."""

import sys
from pathlib import Path

import pytest

from forja.core.adapters import (
    CommandPlan,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolInfo,
    ToolOutput,
    Unparseable,
)
from forja.core.reports import ReportFormat
from forja.languages.java import JavaAdapter, gradle

GRADLE = ("g",)
ADAPTER = JavaAdapter(maven=("m",), gradle=GRADLE)
PLUGINS = """plugins {
    id 'java'
    id 'checkstyle'
    id 'pmd'
    id 'com.github.spotbugs' version '6.0.26'
    id 'com.diffplug.spotless' version '7.0.2'
}
"""


def project(root: Path, build: str = PLUGINS, name: str = "build.gradle") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(build, encoding="utf-8")
    return root


def planned(root: Path, operation: Operation, **request: object) -> CommandPlan:
    result = ADAPTER.plan(operation, OperationRequest(root=root, **request))  # type: ignore[arg-type]
    assert isinstance(result, CommandPlan), result
    return result


def test_a_build_file_makes_it_a_gradle_project(tmp_path: Path) -> None:
    found = ADAPTER.detect(project(tmp_path, name="build.gradle.kts"))
    assert found is not None
    assert found.manifests == ("build.gradle.kts",)
    assert found.toolchain_probe is not None
    assert found.toolchain_probe.plan.argv == (*GRADLE, "-v")
    assert found.toolchain_probe.pattern == gradle.VERSION


def test_a_pom_wins_over_a_build_file(tmp_path: Path) -> None:
    project(tmp_path)
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    found = ADAPTER.detect(tmp_path)
    assert found is not None
    assert found.manifests == ("pom.xml",)
    assert planned(tmp_path, Operation.BUILD).argv[0] == "m"


def test_neither_manifest_is_not_java(tmp_path: Path) -> None:
    assert ADAPTER.detect(tmp_path) is None
    assert isinstance(ADAPTER.plan(Operation.BUILD, OperationRequest(root=tmp_path)), NotSupported)


def test_the_wrapper_of_the_repository_is_used_before_the_path(tmp_path: Path) -> None:
    assert gradle.find_gradle(tmp_path)[0].lower().endswith(("gradle", "gradle.bat", "gradle.exe"))
    wrapper = tmp_path / ("gradlew.bat" if sys.platform == "win32" else "gradlew")
    wrapper.write_text("", encoding="utf-8")
    assert gradle.find_gradle(tmp_path) == (str(wrapper),)


def test_build_and_test(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert planned(root, Operation.BUILD).argv == (
        "g", "--console", "plain", "compileJava", "compileTestJava"
    )  # fmt: skip
    plan = planned(root, Operation.TEST)
    assert plan.argv == ("g", "--console", "plain", "cleanTest", "test")
    assert plan.cwd == root
    assert plan.config_files == gradle.CONFIG_FILES
    assert {"JAVA_HOME", "GRADLE_USER_HOME"} <= plan.env_allow


def test_coverage_continues_past_failing_tests_and_names_jacoco_in_an_init_script(
    tmp_path: Path,
) -> None:
    argv = planned(project(tmp_path), Operation.COVERAGE).argv
    assert "--continue" in argv
    assert argv[-3:] == ("cleanTest", "test", "jacocoTestReport")
    script = Path(argv[argv.index("-I") + 1])
    assert script.is_file()
    assert "jacoco" in script.read_text(encoding="utf-8")


def test_lint_runs_the_declared_linters_only(tmp_path: Path) -> None:
    argv = planned(project(tmp_path), Operation.LINT).argv
    assert argv[-3:] == ("checkstyleMain", "pmdMain", "spotbugsMain")
    only = planned(project(tmp_path / "b", "plugins { id 'checkstyle' }"), Operation.LINT).argv
    assert only[-1] == "checkstyleMain"
    assert "pmdMain" not in only
    scripts = [Path(only[i + 1]).name for i, part in enumerate(only) if part == "-I"]
    assert scripts == ["lint.init.gradle", "spotbugs.init.gradle"]


def test_a_plugin_name_inside_another_word_is_not_the_plugin(tmp_path: Path) -> None:
    root = project(tmp_path, "// not a linter: pmdx, mycheckstyles\n")
    assert isinstance(ADAPTER.plan(Operation.LINT, OperationRequest(root=root)), NotSupported)


@pytest.mark.parametrize("operation", [Operation.FORMAT, Operation.LINT])
def test_an_undeclared_tool_is_not_supported(tmp_path: Path, operation: Operation) -> None:
    result = ADAPTER.plan(operation, OperationRequest(root=project(tmp_path, "plugins {}")))
    assert isinstance(result, NotSupported)
    assert "does not declare" in result.reason


def test_format_apply_to_one_file_goes_through_the_ide_hook(tmp_path: Path) -> None:
    root = project(tmp_path)
    apply = planned(root, Operation.FORMAT, files=("src/main/java/A.java",), check_only=False)
    assert apply.argv[-2:] == (
        f"-PspotlessIdeHook={root / 'src/main/java/A.java'}",
        "spotlessApply",
    )


def test_format_check_asks_the_project_because_the_hook_names_no_file(tmp_path: Path) -> None:
    argv = planned(
        project(tmp_path), Operation.FORMAT, files=("src/main/java/A.java",), check_only=True
    ).argv
    assert argv == ("g", "--console", "plain", "spotlessCheck")


@pytest.mark.parametrize("files", [(), ("A.java", "B.java")])
def test_format_apply_to_none_or_several_files_goes_through_the_project(
    tmp_path: Path, files: tuple[str, ...]
) -> None:
    argv = planned(project(tmp_path), Operation.FORMAT, files=files, check_only=False).argv
    assert argv == ("g", "--console", "plain", "spotlessApply")


def test_filtering_coverage_is_not_offered(tmp_path: Path) -> None:
    result = ADAPTER.plan(
        Operation.COVERAGE, OperationRequest(root=project(tmp_path), tests=("a.B.c",))
    )
    assert isinstance(result, NotSupported)


def test_what_it_reports_follows_the_build_tool(tmp_path: Path) -> None:
    adapter = JavaAdapter(maven=("m",), gradle=GRADLE)
    adapter.detect(project(tmp_path))
    spec = adapter.report(Operation.COVERAGE)
    assert not isinstance(spec, NotSupported)
    assert spec.format is ReportFormat.JACOCO_XML
    assert spec.path == gradle.COVERAGE_REPORT
    info = adapter.tool_info(Operation.TEST)
    assert isinstance(info, ToolInfo)
    assert info.tool == "gradle"
    assert info.tool_probe.pattern == gradle.VERSION


# -- reading what Gradle leaves behind ----------------------------------------------------------

FIXTURES = Path(__file__).parents[2] / "fixtures" / "java" / "gradle"
WORK = Path("C:/work/calc")
BACKSLASHED = chr(92).join(("C:", "work", "calc"))


def localize(text: str, root: Path) -> str:
    return text.replace(BACKSLASHED, str(root)).replace("C:/work/calc", root.as_posix())


def gradle_adapter(root: Path) -> JavaAdapter:
    adapter = JavaAdapter(maven=("m",), gradle=GRADLE)
    adapter.detect(project(root))
    return adapter


def log_of(tmp_path: Path, name: str, root: Path) -> Path:
    log = tmp_path / name
    log.write_text(localize((FIXTURES / name).read_text(encoding="utf-8"), root), encoding="utf-8")
    return log


def put(root: Path, relative: str, name: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    text = localize((FIXTURES / name).read_text(encoding="utf-8"), root)
    target.write_text(text, encoding="utf-8")


def facts(result: object) -> list[tuple[str, str, object]]:
    assert isinstance(result, Parsed), result
    return [(d.measurement, d.target.id, d.value) for d in result.drafts]


def test_a_compile_error_is_the_file_javac_names(tmp_path: Path) -> None:
    root = tmp_path / "w"
    log = log_of(tmp_path, "build-error.txt", root)
    result = gradle_adapter(root).parse(Operation.BUILD, ToolOutput(1, log, root))
    assert facts(result) == [("build.compile_error", "src/main/java/com/acme/Broken.java", 1)]


def test_a_failed_build_with_no_javac_line_is_a_failed_build(tmp_path: Path) -> None:
    log = tmp_path / "b.log"
    log.write_text("FAILURE: Build failed with an exception.", encoding="utf-8")
    result = gradle_adapter(tmp_path / "w").parse(Operation.BUILD, ToolOutput(1, log, tmp_path))
    assert facts(result) == [("build.failed", "build", 1)]


def test_the_test_outcomes_are_read_from_the_build_directory(tmp_path: Path) -> None:
    root = tmp_path / "w"
    put(root, "build/test-results/test/TEST-com.acme.CalcTest.xml", "TEST-fail.xml")
    log = tmp_path / "t.log"
    log.write_text("", encoding="utf-8")
    result = gradle_adapter(root).parse(Operation.TEST, ToolOutput(1, log, root))
    assert facts(result) == [
        ("test.outcome", "com.acme.CalcTest.adds", "passed"),
        ("test.outcome", "com.acme.CalcTest.fails", "failed"),
    ]


def test_a_passing_run_whose_reports_list_a_failure_is_not_understood(tmp_path: Path) -> None:
    root = tmp_path / "w"
    put(root, "build/test-results/test/TEST-com.acme.CalcTest.xml", "TEST-fail.xml")
    log = tmp_path / "t.log"
    log.write_text("", encoding="utf-8")
    result = gradle_adapter(root).parse(Operation.TEST, ToolOutput(0, log, root))
    assert isinstance(result, Unparseable)


def test_no_test_report_is_no_test_collected(tmp_path: Path) -> None:
    log = tmp_path / "t.log"
    log.write_text("", encoding="utf-8")
    result = gradle_adapter(tmp_path / "w").parse(Operation.TEST, ToolOutput(0, log, tmp_path))
    assert facts(result) == [("test.collected", "tests", 0)]


def test_spotless_lists_the_files_without_the_diff_lines(tmp_path: Path) -> None:
    root = tmp_path / "w"
    log = log_of(tmp_path, "format.txt", root)
    result = gradle_adapter(root).parse(Operation.FORMAT, ToolOutput(1, log, root))
    assert facts(result) == [
        ("format.unformatted", "src/main/java/com/acme/Bad.java", 1),
        ("format.unformatted", "src/test/java/com/acme/CalcTest.java", 1),
    ]


def test_lint_reads_checkstyle_and_spotbugs_from_their_reports(tmp_path: Path) -> None:
    root = tmp_path / "w"
    put(root, "build/reports/checkstyle/main.xml", "checkstyle.xml")
    put(root, "build/reports/spotbugs/main.xml", "spotbugs.xml")
    bad = root / "src" / "main" / "java" / "com" / "acme"
    bad.mkdir(parents=True)
    (bad / "Bad.java").write_text("class Bad {}", encoding="utf-8")
    log = tmp_path / "l.log"
    log.write_text("", encoding="utf-8")
    result = facts(gradle_adapter(root).parse(Operation.LINT, ToolOutput(0, log, root)))
    path = "src/main/java/com/acme/Bad.java"
    assert ("lint.checkstyle.LineLength", path, 1) in result
    assert ("lint.spotbugs.ES_COMPARING_PARAMETER_STRING_WITH_EQ", path, 1) in result
    assert ("lint.checkstyle.LineLength", "src/main/java/com/acme/Calc.java", 1) in result


def test_lint_that_failed_without_a_report_is_not_understood(tmp_path: Path) -> None:
    log = tmp_path / "l.log"
    log.write_text("FAILURE: Build failed", encoding="utf-8")
    result = gradle_adapter(tmp_path / "w").parse(Operation.LINT, ToolOutput(1, log, tmp_path))
    assert isinstance(result, Unparseable)


def test_a_checkstyle_source_without_a_package_is_the_rule_itself(tmp_path: Path) -> None:
    root = tmp_path / "w"
    report = root / "build" / "reports" / "checkstyle" / "main.xml"
    report.parent.mkdir(parents=True)
    report.write_text(
        '<checkstyle><file name="Calc.java">'
        '<error line="1" severity="warning" message="m" source="Custom"/></file></checkstyle>',
        encoding="utf-8",
    )
    log = tmp_path / "l.log"
    log.write_text("", encoding="utf-8")

    result = gradle_adapter(root).parse(Operation.LINT, ToolOutput(0, log, root))

    assert ("lint.checkstyle.Custom", "Calc.java", 1) in facts(result)


def test_before_any_detection_the_adapter_reports_maven(tmp_path: Path) -> None:
    adapter = JavaAdapter(maven=("m",), gradle=GRADLE)

    spec = adapter.report(Operation.COVERAGE)

    assert not isinstance(spec, NotSupported)
    assert spec.path != gradle.COVERAGE_REPORT


def test_lint_adds_the_findings_of_pmd_to_the_others(tmp_path: Path) -> None:
    root = tmp_path / "w"
    put(root, "build/reports/checkstyle/main.xml", "checkstyle.xml")
    put(root, "build/reports/spotbugs/main.xml", "spotbugs.xml")
    pmd = root / "build" / "reports" / "pmd" / "main.xml"
    pmd.parent.mkdir(parents=True)
    pmd.write_text(
        '<pmd xmlns="http://pmd.sourceforge.net/report/2.0.0"><file name="Bad.java">'
        '<violation beginline="1" rule="UnusedPrivateField"/></file></pmd>',
        encoding="utf-8",
    )
    log = tmp_path / "l.log"
    log.write_text("", encoding="utf-8")

    result = facts(gradle_adapter(root).parse(Operation.LINT, ToolOutput(0, log, root)))

    assert ("lint.pmd.UnusedPrivateField", "Bad.java", 1) in result
    assert any(measurement.startswith("lint.spotbugs") for measurement, _, _ in result)
    assert any(measurement.startswith("lint.checkstyle") for measurement, _, _ in result)
