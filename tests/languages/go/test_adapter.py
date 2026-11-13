"""The Go adapter: the shared promises, the commands it plans, and what it reads back.

The files in ``tests/fixtures/go`` are what the real tools printed on a small module.
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
from forja.core.adapters.common import PROPERTY_SEED
from forja.core.reports import ReportFormat, ReportSpec
from forja.core.reports.readers import read_coverage
from forja.languages.go import GoAdapter
from forja.languages.go.adapter import COVERAGE_REPORT
from tests.core.adapters.conformance import language_problems

FIXTURES = Path(__file__).parents[2] / "fixtures" / "go"
ADAPTER = GoAdapter(go=("g",), gofmt=("gf",))
LINTING = GoAdapter(go=("g",), gofmt=("gf",), golangci_lint=("gl",))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "go.mod").write_text("module example.com/calc\n\ngo 1.22\n", encoding="utf-8")
    return root


def read(operation: Operation, name: str, code: int | None) -> ParseResult:
    return ADAPTER.parse(operation, ToolOutput(code, FIXTURES / name))


def facts(result: object) -> list[tuple[str, str, object]]:
    assert isinstance(result, Parsed), result
    return [(d.measurement, d.target.id, d.value) for d in result.drafts]


# -- the shared promises ------------------------------------------------------------------------


@pytest.mark.parametrize("adapter", [GoAdapter(), LINTING])
def test_the_adapter_keeps_every_promise_of_the_interface(
    adapter: GoAdapter, project: Path, tmp_path: Path
) -> None:
    assert language_problems(adapter, project, tmp_path / "scratch") == []


# -- finding and classifying ----------------------------------------------------------------------


def test_a_project_is_found_by_its_manifest(project: Path, tmp_path: Path) -> None:
    found = ADAPTER.detect(project)

    assert found is not None
    assert (found.language, found.roots, found.manifests) == ("go", (".",), ("go.mod",))
    assert found.toolchain_probe is not None
    assert found.toolchain_probe.plan.argv == ("g", "version")
    assert ADAPTER.detect(tmp_path / "nowhere") is None


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("calc.go", FileClass.PRODUCTION),
        ("pkg/sub/calc.go", FileClass.PRODUCTION),
        ("calc_test.go", FileClass.TEST),
        ("pkg\\calc_test.go", FileClass.TEST),
        ("pkg/testdata/sample.go", FileClass.FIXTURE),
        ("vendor/example.com/dep/dep.go", None),
        ("pkg/_scratch.go", None),
        ("pkg/.hidden_test.go", None),
        ("_tools/main.go", None),
        ("./pkg/a.go", FileClass.PRODUCTION),
        ("go.mod", None),
        ("go.sum", None),
        ("README.md", None),
    ],
)
def test_files_are_classified_by_how_they_are_named_and_where_they_live(
    path: str, kind: FileClass | None
) -> None:
    assert ADAPTER.classify(path) is kind


# -- commands --------------------------------------------------------------------------------------


def plan(adapter: GoAdapter, operation: Operation, project: Path, **options: object) -> object:
    return adapter.plan(operation, OperationRequest(root=project, **options))  # type: ignore[arg-type]


def argv(operation: Operation, project: Path, **options: object) -> tuple[str, ...]:
    answer = plan(LINTING, operation, project, **options)
    assert isinstance(answer, CommandPlan), answer
    return answer.argv


def test_rapid_is_seeded_and_writes_no_fail_file_through_the_environment(project: Path) -> None:
    answer = plan(LINTING, Operation.TEST, project)

    assert isinstance(answer, CommandPlan)
    assert answer.env_set["RAPID_SEED"] == str(PROPERTY_SEED)
    assert answer.env_set["RAPID_NOFAILFILE"] == "true"
    assert not any("rapid" in part for part in answer.argv)


def test_format_lists_what_is_not_canonical_and_rewrites_only_when_asked(project: Path) -> None:
    assert argv(Operation.FORMAT, project) == ("gf", "-l", "-w", "--", ".")
    assert argv(Operation.FORMAT, project, files=("a.go",), check_only=True) == (
        "gf", "-l", "--", "a.go",
    )  # fmt: skip


def test_lint_runs_golangci_lint_and_is_not_supported_without_it(project: Path) -> None:
    assert argv(Operation.LINT, project) == ("gl", "run", "--color=never")
    # a package is type-checked whole, so the files go as the directories that hold them
    assert argv(Operation.LINT_FIX, project, files=("a.go", "b.go", "pkg/c.go")) == (
        "gl", "run", "--color=never", "--fix", ".", "./pkg",
    )  # fmt: skip
    for operation in (Operation.LINT, Operation.LINT_FIX):
        assert isinstance(plan(ADAPTER, operation, project), NotSupported)
        assert isinstance(ADAPTER.tool_info(operation), NotSupported)


def test_build_and_tests_cover_every_package_without_the_test_cache(project: Path) -> None:
    assert argv(Operation.BUILD, project) == ("g", "build", "./...")
    assert argv(Operation.TEST, project) == ("g", "test", "-count=1", "-json", "./...")


def test_coverage_measures_every_package_and_writes_a_go_profile(project: Path) -> None:
    assert argv(Operation.COVERAGE, project) == (
        "g", "test", "-count=1", "-json", "-coverpkg=./...", f"-coverprofile={COVERAGE_REPORT}",
        "./...",
    )  # fmt: skip
    assert ADAPTER.report(Operation.COVERAGE) == ReportSpec(
        ReportFormat.GO_COVERPROFILE, COVERAGE_REPORT
    )
    assert isinstance(ADAPTER.report(Operation.MUTATION), NotSupported)


def test_the_tests_are_selected_by_package_so_no_filter_is_offered(project: Path) -> None:
    for operation in (Operation.TEST, Operation.COVERAGE):
        assert isinstance(
            plan(ADAPTER, operation, project, test_files=("a_test.go",)), NotSupported
        )
        assert isinstance(plan(ADAPTER, operation, project, tests=("TestA",)), NotSupported)


def test_the_go_tool_chain_may_read_its_own_settings_from_the_environment(project: Path) -> None:
    answer = plan(ADAPTER, Operation.TEST, project)

    assert isinstance(answer, CommandPlan)
    assert {"LOCALAPPDATA", "GOPATH", "GOCACHE"} <= answer.env_allow
    assert "go.mod" in answer.config_files


def test_every_tool_names_its_version_probe() -> None:
    info = LINTING.tool_info(Operation.LINT)

    assert isinstance(info, ToolInfo)
    assert info.tool_probe.plan.argv == ("gl", "--version")
    fmt = LINTING.tool_info(Operation.FORMAT)
    assert isinstance(fmt, ToolInfo)
    assert fmt.tool == "gofmt"
    assert isinstance(LINTING.tool_info(Operation.MUTATION), NotSupported)


# -- reading what they print ---------------------------------------------------------------------


def test_the_outcome_of_every_test_is_read_from_the_json_stream() -> None:
    result = read(Operation.TEST, "test-pass.jsonl", 0)

    assert facts(result) == [
        ("test.outcome", "example.com/calc/TestAdd", "passed"),
        ("test.outcome", "example.com/calc/TestSignPositive", "passed"),
    ]


def test_a_failed_test_is_a_failed_outcome() -> None:
    result = read(Operation.TEST, "test-fail.jsonl", 1)

    assert facts(result) == [("test.outcome", "example.com/calc/TestAdd", "failed")]


def test_a_package_that_does_not_compile_is_one_failed_outcome() -> None:
    result = read(Operation.TEST, "test-build-error.jsonl", 1)

    assert facts(result) == [("test.outcome", "example.com/calc", "failed")]


def test_test_output_that_contradicts_the_exit_code_is_not_understood() -> None:
    assert isinstance(read(Operation.TEST, "test-fail.jsonl", 0), Unparseable)
    assert isinstance(read(Operation.TEST, "test-pass.jsonl", 1), Unparseable)
    assert isinstance(read(Operation.TEST, "test-pass.jsonl", 2), Unparseable)
    assert isinstance(read(Operation.TEST, "test-pass.jsonl", None), Unparseable)


def test_a_skipped_test_is_counted(tmp_path: Path) -> None:
    log = tmp_path / "t.log"
    log.write_text(
        '[out] {"Action":"skip","Package":"p","Test":"TestA"}\n'
        '[out] {"Action":"pass","Package":"p"}\n',
        encoding="utf-8",
    )

    result = ADAPTER.parse(Operation.TEST, ToolOutput(0, log))

    assert facts(result) == [
        ("test.outcome", "p/TestA", "skipped"),
        ("test.skipped", "tests", 1),
    ]


def test_a_module_without_tests_collects_none(tmp_path: Path) -> None:
    log = tmp_path / "t.log"
    log.write_text('{"Action":"skip","Package":"p"}\n', encoding="utf-8")

    result = ADAPTER.parse(Operation.TEST, ToolOutput(0, log))

    assert facts(result) == [("test.collected", "tests", 0)]


def test_a_failing_package_does_not_hide_behind_the_package_that_shares_its_prefix(
    tmp_path: Path,
) -> None:
    log = tmp_path / "t.log"
    log.write_text(
        '{"Action":"fail","Package":"m","Test":"TestA"}\n'
        '{"Action":"fail","Package":"m"}\n'
        '{"Action":"fail","Package":"m/sub"}\n',
        encoding="utf-8",
    )

    result = ADAPTER.parse(Operation.TEST, ToolOutput(1, log))

    assert facts(result) == [
        ("test.outcome", "m/TestA", "failed"),
        ("test.outcome", "m/sub", "failed"),
    ]


def test_build_errors_are_reported_per_file() -> None:
    assert facts(read(Operation.BUILD, "build-error.txt", 1)) == [
        ("build.compile_error", "bad.go", 1)
    ]
    assert facts(read(Operation.BUILD, "build-error.txt", 0)) == []
    assert isinstance(read(Operation.BUILD, "build-error.txt", None), Unparseable)


def test_a_build_failure_without_a_file_is_one_failure_of_the_build(tmp_path: Path) -> None:
    log = tmp_path / "b.log"
    log.write_text("go: cannot find main module\n", encoding="utf-8")

    result = ADAPTER.parse(Operation.BUILD, ToolOutput(1, log))

    assert facts(result) == [("build.failed", "build", 1)]


def test_gofmt_lists_unformatted_files_and_files_it_cannot_read() -> None:
    assert facts(read(Operation.FORMAT, "gofmt.txt", 2)) == [
        ("format.invalid_syntax", "broken.go", 1),
        ("format.unformatted", "ugly.go", 1),
    ]
    assert isinstance(read(Operation.FORMAT, "gofmt.txt", 3), Unparseable)


def test_lint_findings_are_counted_per_file_and_linter(tmp_path: Path) -> None:
    log = tmp_path / "l.log"
    log.write_text(
        "[out] calc.go:3:6: exported func Add should have comment (revive)\n"
        "[out] calc.go:9:2: ineffectual assignment to x (ineffassign)\n"
        "[out] calc.go:12:2: ineffectual assignment to y (ineffassign)\n"
        "[out]     x := 1\n",
        encoding="utf-8",
    )

    result = LINTING.parse(Operation.LINT, ToolOutput(1, log))

    assert facts(result) == [
        ("lint.ineffassign", "calc.go", 2),
        ("lint.revive", "calc.go", 1),
    ]
    assert facts(LINTING.parse(Operation.LINT, ToolOutput(0, tmp_path / "none"))) == []
    assert isinstance(LINTING.parse(Operation.LINT, ToolOutput(1, tmp_path / "none")), Unparseable)
    assert isinstance(LINTING.parse(Operation.LINT, ToolOutput(3, log)), Unparseable)


def test_the_real_cover_profile_names_the_lines_the_tests_ran(tmp_path: Path) -> None:
    (tmp_path / "calc.go").write_text("package calc\n", encoding="utf-8")
    text = (FIXTURES / "cover.out").read_text(encoding="utf-8")

    report = read_coverage(ReportFormat.GO_COVERPROFILE, text, tmp_path)

    calc = report.of("calc.go")
    assert calc is not None
    assert calc.missed == frozenset({9, 10})  # the `return -1` of a branch no test takes
    assert {4, 5, 8, 11} <= calc.covered


def test_the_real_golangci_lint_report_is_counted_and_a_typecheck_failure_is_not_a_pass() -> None:
    issues = LINTING.parse(Operation.LINT, ToolOutput(1, FIXTURES / "lint-issues.txt"))
    broken = LINTING.parse(Operation.LINT, ToolOutput(7, FIXTURES / "lint-typecheck.txt"))

    assert facts(issues) == [("lint.errcheck", "l.go", 1), ("lint.ineffassign", "l.go", 1)]
    assert isinstance(broken, Unparseable)


def test_without_golangci_lint_the_lint_cannot_be_measured_and_everything_else_can(
    project: Path,
) -> None:
    assert ADAPTER.unmeasurable(Operation.LINT, project) == (
        "golangci-lint is not installed (forja instalar installs it)"
    )
    for operation in (Operation.FORMAT, Operation.LINT_FIX, Operation.BUILD, Operation.TEST):
        assert ADAPTER.unmeasurable(operation, project) is None
    assert LINTING.unmeasurable(Operation.LINT, project) is None
