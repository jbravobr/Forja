"""The C# adapter: the shared promises, the commands it plans, and what it reads back.

The files in ``tests/fixtures/csharp`` are what the real tools printed on a small solution
(.NET SDK 9.0.319, xUnit 2.9.2, coverlet.collector 6.0.2); the project's directory is
``/work/calc``.
"""

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
from forja.languages.csharp import CSharpAdapter
from forja.languages.csharp.adapter import COVERAGE_DIRECTORY, COVERAGE_REPORT, artifacts_dir
from forja.languages.csharp.parsers import MAX_LINE
from tests.core.adapters.conformance import language_problems

FIXTURES = Path(__file__).parents[2] / "fixtures" / "csharp"
ADAPTER = CSharpAdapter(dotnet=("d",))
WORK = Path("/work/calc")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "Calc.sln").write_text("", encoding="utf-8")
    return root


def read(operation: Operation, name: str, code: int | None) -> ParseResult:
    return ADAPTER.parse(operation, ToolOutput(code, FIXTURES / name, WORK))


def facts(result: object) -> list[tuple[str, str, object]]:
    assert isinstance(result, Parsed), result
    return [(d.measurement, d.target.id, d.value) for d in result.drafts]


# -- the shared promises ------------------------------------------------------------------------


@pytest.mark.parametrize("adapter", [CSharpAdapter(), ADAPTER])
def test_the_adapter_keeps_every_promise_of_the_interface(
    adapter: CSharpAdapter, project: Path, tmp_path: Path
) -> None:
    assert language_problems(adapter, project, tmp_path / "scratch") == []


# -- finding and classifying ----------------------------------------------------------------------


def test_a_project_is_found_by_its_solution_or_project_file(project: Path, tmp_path: Path) -> None:
    found = ADAPTER.detect(project)

    assert found is not None
    assert (found.language, found.roots, found.manifests) == ("csharp", (".",), ("Calc.sln",))
    assert found.toolchain_probe is not None
    assert found.toolchain_probe.plan.argv == ("d", "--version")
    assert ADAPTER.detect(tmp_path / "nowhere") is None
    (project / "Calc.sln").unlink()
    assert ADAPTER.detect(project) is None
    (project / "App.csproj").write_text("", encoding="utf-8")
    assert ADAPTER.detect(project) is not None


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("src/Calc/Calculator.cs", FileClass.PRODUCTION),
        ("Calculator.cs", FileClass.PRODUCTION),
        ("tests/Calc.Tests/CalculatorTests.cs", FileClass.TEST),
        ("Calc.Tests/Helpers.cs", FileClass.TEST),
        ("src\\Calc\\CalculatorTest.cs", FileClass.TEST),
        ("Calc.Tests/TestData/Sample.cs", FileClass.FIXTURE),
        ("src/Calc/obj/Debug/net9.0/Calc.AssemblyInfo.cs", None),
        ("src/Calc/bin/Debug/Calc.cs", None),
        ("src/Calc/Resources.g.cs", None),
        ("Calc.csproj", None),
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


def test_format_checks_or_rewrites_whitespace_of_the_one_solution(project: Path) -> None:
    assert argv(Operation.FORMAT, project) == ("d", "format", "whitespace", "Calc.sln")
    assert argv(Operation.FORMAT, project, files=("a.cs",), check_only=True) == (
        "d", "format", "whitespace", "Calc.sln", "--verify-no-changes", "--include", "a.cs",
    )  # fmt: skip


def test_lint_runs_the_roslyn_analyzers_and_fixes_only_when_asked(project: Path) -> None:
    assert argv(Operation.LINT, project) == (
        "d", "format", "analyzers", "Calc.sln", "--verify-no-changes",
    )  # fmt: skip
    assert argv(Operation.LINT_FIX, project, files=("a.cs",)) == (
        "d", "format", "analyzers", "Calc.sln", "--include", "a.cs",
    )  # fmt: skip


def test_build_and_tests_run_on_the_solution_with_one_line_per_test(project: Path) -> None:
    logger = ("--logger", "console;verbosity=normal")

    assert argv(Operation.BUILD, project) == ("d", "build", "Calc.sln", "--nologo")
    assert argv(Operation.TEST, project) == ("d", "test", "Calc.sln", "--nologo", *logger)


def test_coverage_uses_the_collector_and_reads_a_cobertura_report(project: Path) -> None:
    assert argv(Operation.COVERAGE, project) == (
        "d", "test", "Calc.sln", "--nologo", "--logger", "console;verbosity=normal",
        "--collect:XPlat Code Coverage", "--results-directory", COVERAGE_DIRECTORY,
    )  # fmt: skip
    assert ADAPTER.report(Operation.COVERAGE) == ReportSpec(
        ReportFormat.COBERTURA_XML, COVERAGE_REPORT
    )
    assert isinstance(ADAPTER.report(Operation.MUTATION), NotSupported)


def test_a_solution_is_preferred_and_two_candidates_are_not_chosen_for_the_person(
    project: Path,
) -> None:
    (project / "App.csproj").write_text("", encoding="utf-8")
    assert argv(Operation.BUILD, project)[2] == "Calc.sln"
    (project / "Other.sln").write_text("", encoding="utf-8")
    assert isinstance(plan(Operation.BUILD, project), NotSupported)
    (project / "Calc.sln").unlink()
    (project / "Other.sln").unlink()
    (project / "More.csproj").write_text("", encoding="utf-8")
    assert isinstance(plan(Operation.BUILD, project), NotSupported)


def test_a_project_alone_is_enough(tmp_path: Path) -> None:
    (tmp_path / "App.csproj").write_text("", encoding="utf-8")

    assert argv(Operation.BUILD, tmp_path) == ("d", "build", "App.csproj", "--nologo")
    assert isinstance(plan(Operation.BUILD, tmp_path / "nowhere"), NotSupported)


def test_no_filter_of_the_tests_is_offered(project: Path) -> None:
    for operation in (Operation.TEST, Operation.COVERAGE):
        assert isinstance(plan(operation, project, tests=("Adds",)), NotSupported)
        assert isinstance(plan(operation, project, test_files=("a.cs",)), NotSupported)


def test_dotnet_may_read_its_own_settings_from_the_environment(project: Path) -> None:
    answer = plan(Operation.TEST, project)

    assert isinstance(answer, CommandPlan)
    assert {"LOCALAPPDATA", "NUGET_PACKAGES", "DOTNET_ROOT"} <= answer.env_allow
    assert answer.env_set["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"
    assert ".editorconfig" in answer.config_files


def test_the_build_writes_obj_and_bin_outside_the_worktree_one_place_per_worktree(
    project: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "Calc.sln").write_text("", encoding="utf-8")
    here, there = plan(Operation.TEST, project), plan(Operation.TEST, other)

    assert isinstance(here, CommandPlan) and isinstance(there, CommandPlan)
    assert here.env_set["UseArtifactsOutput"] == "true"
    assert here.env_set["ArtifactsPath"] == str(artifacts_dir(project))
    assert not artifacts_dir(project).is_relative_to(project)
    assert here.env_set["ArtifactsPath"] != there.env_set["ArtifactsPath"]
    assert artifacts_dir(project) == artifacts_dir(project)


def test_the_build_cache_is_the_local_app_data_on_windows_and_the_home_cache_elsewhere(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    local = tmp_path / "local"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    monkeypatch.setattr("sys.platform", "win32")
    assert artifacts_dir(project).parent == local / "forja" / "dotnet"

    monkeypatch.setattr("sys.platform", "linux")
    assert artifacts_dir(project).parent == home / ".cache" / "forja" / "dotnet"

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA")
    assert artifacts_dir(project).parent == home / ".cache" / "forja" / "dotnet"


def test_every_tool_names_its_version_probe() -> None:
    info = ADAPTER.tool_info(Operation.BUILD)

    assert isinstance(info, ToolInfo)
    assert info.tool_probe.plan.argv == ("d", "--version")
    fmt = ADAPTER.tool_info(Operation.FORMAT)
    assert isinstance(fmt, ToolInfo)
    assert fmt.tool == "dotnet format"
    assert isinstance(ADAPTER.tool_info(Operation.MUTATION), NotSupported)


# -- reading what they print ---------------------------------------------------------------------


def test_the_outcome_of_every_test_is_read_one_line_each() -> None:
    assert facts(read(Operation.TEST, "test-pass.txt", 0)) == [
        ("test.outcome", "Calc.Tests.CalculatorTests.AddsMany(a: 1, b: 1, expected: 2)", "passed"),
        ("test.outcome", "Calc.Tests.CalculatorTests.AddsMany(a: 2, b: 2, expected: 4)", "passed"),
        ("test.outcome", "Calc.Tests.CalculatorTests.AddsTwoNumbers", "passed"),
        ("test.outcome", "Calc.Tests.CalculatorTests.Later", "skipped"),
        ("test.skipped", "tests", 1),
    ]


def test_a_failed_test_is_a_failed_outcome() -> None:
    result = facts(read(Operation.TEST, "test-fail.txt", 1))

    assert ("test.outcome", "Calc.Tests.CalculatorTests.AddsTwoNumbers", "failed") in result


def test_test_output_that_contradicts_the_exit_code_is_not_understood() -> None:
    assert isinstance(read(Operation.TEST, "test-fail.txt", 0), Unparseable)
    assert isinstance(read(Operation.TEST, "test-pass.txt", 1), Unparseable)
    assert isinstance(read(Operation.TEST, "test-pass.txt", 2), Unparseable)
    assert isinstance(read(Operation.TEST, "test-pass.txt", None), Unparseable)
    assert isinstance(read(Operation.TEST, "test-build-error.txt", 1), Unparseable)


def test_a_solution_without_tests_collects_none(tmp_path: Path) -> None:
    log = tmp_path / "t.log"
    log.write_text("Test Run Successful.\nTotal tests: 0\n", encoding="utf-8")

    assert facts(ADAPTER.parse(Operation.TEST, ToolOutput(0, log))) == [
        ("test.collected", "tests", 0)
    ]


def test_build_errors_are_reported_per_file_relative_to_the_project() -> None:
    assert facts(read(Operation.BUILD, "build-error.txt", 1)) == [
        ("build.compile_error", "src/Calc/Bad.cs", 1)
    ]
    assert facts(read(Operation.BUILD, "test-build-error.txt", 1)) == [
        ("build.compile_error", "tests/Calc.Tests/Broken.cs", 1)
    ]
    assert facts(read(Operation.BUILD, "build-error.txt", 0)) == []
    assert isinstance(read(Operation.BUILD, "build-error.txt", None), Unparseable)


def test_a_build_failure_without_a_file_is_one_failure_of_the_build(tmp_path: Path) -> None:
    log = tmp_path / "b.log"
    log.write_text("MSBUILD : error MSB1009: Project file does not exist.\n", encoding="utf-8")

    assert facts(ADAPTER.parse(Operation.BUILD, ToolOutput(1, log, WORK))) == [
        ("build.failed", "build", 1)
    ]


def test_format_lists_the_files_that_are_not_in_the_projects_form() -> None:
    assert facts(read(Operation.FORMAT, "format.txt", 2)) == [
        ("format.unformatted", "src/Calc/Ugly.cs", 1)
    ]
    assert facts(read(Operation.FORMAT, "format.txt", 0)) != []
    assert isinstance(read(Operation.FORMAT, "format.txt", 3), Unparseable)


def test_analyzer_findings_are_counted_per_file_and_rule() -> None:
    assert facts(read(Operation.LINT, "lint.txt", 2)) == [("lint.CA1822", "src/Calc/Ugly.cs", 2)]
    assert isinstance(read(Operation.LINT, "lint.txt", 3), Unparseable)


def test_a_path_outside_the_project_is_named_by_its_file_only(tmp_path: Path) -> None:
    log = tmp_path / "f.log"
    log.write_text("/elsewhere/dir/X.cs(1,1): error WHITESPACE: Fix.\n", encoding="utf-8")

    assert facts(ADAPTER.parse(Operation.FORMAT, ToolOutput(2, log, WORK))) == [
        ("format.unformatted", "X.cs", 1)
    ]


def test_the_real_cobertura_report_names_the_lines_the_tests_ran(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Calc"
    source.mkdir(parents=True)
    (source / "Calc.cs").write_text("namespace Calc;\n", encoding="utf-8")
    where = str(tmp_path.as_posix()).split(":", 1)[-1].lstrip("/").replace("/", "\\")
    text = (FIXTURES / "coverage.cobertura.xml").read_text(encoding="utf-8")
    text = text.replace("work\\calc", where)

    report = read_coverage(ReportFormat.COBERTURA_XML, text, tmp_path)

    calc = report.of("src/Calc/Calc.cs")
    assert calc is not None
    assert {6, 7, 8} <= calc.covered
    assert not calc.missed


@pytest.mark.parametrize("operation", [Operation.FORMAT, Operation.LINT])
def test_changes_reported_in_no_line_the_parser_knows_are_not_understood(
    operation: Operation, tmp_path: Path
) -> None:
    log = tmp_path / "f.log"
    log.write_text("Formatted code file 'Ugly.cs'.\n", encoding="utf-8")

    assert isinstance(ADAPTER.parse(operation, ToolOutput(2, log, WORK)), Unparseable)
    assert facts(ADAPTER.parse(operation, ToolOutput(0, log, WORK))) == []


def test_a_line_of_exactly_the_limit_is_still_read_and_a_longer_one_is_not(
    tmp_path: Path,
) -> None:
    def outcome_of(name: str) -> list[tuple[str, str, object]]:
        log = tmp_path / "t.log"
        log.write_text(f"  Passed {name} [1 ms]\n", encoding="utf-8")
        return facts(ADAPTER.parse(Operation.TEST, ToolOutput(0, log, WORK)))

    longest = "T" * (MAX_LINE - len("  Passed  [1 ms]"))

    assert outcome_of(longest) == [("test.outcome", longest, "passed")]
    assert outcome_of(longest + "T") == [("test.collected", "tests", 0)]
