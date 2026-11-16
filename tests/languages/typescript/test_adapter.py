"""The JavaScript/TypeScript adapter: the shared promises, the commands it plans, and what it reads.

The files in ``tests/fixtures/typescript`` are what the real tools printed on a small project
(Vitest 5.0.5, Jest 30, Prettier 3.9, ESLint 10.13, TypeScript 6.0).
"""

import json
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
from forja.languages.typescript import TypeScriptAdapter
from forja.languages.typescript.adapter import (
    COVERAGE_DIRECTORY,
    COVERAGE_REPORT,
    ESLINT_FORMATTER,
    JEST_REPORTER,
)
from tests.core.adapters.conformance import language_problems

FIXTURES = Path(__file__).parents[2] / "fixtures" / "typescript"
ADAPTER = TypeScriptAdapter(node=("n",))
ALL_TOOLS = {"prettier": "3", "eslint": "10", "typescript": "6"}


def make_project(root: Path, **dependencies: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    document = {"name": "calc", "devDependencies": {**ALL_TOOLS, **dependencies}}
    (root / "package.json").write_text(json.dumps(document), encoding="utf-8")
    (root / "tsconfig.json").write_text("{}", encoding="utf-8")
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path / "project", vitest="5")


@pytest.fixture
def jest_project(tmp_path: Path) -> Path:
    return make_project(tmp_path / "jest-project", jest="30")


def read(operation: Operation, name: str, code: int | None) -> ParseResult:
    return ADAPTER.parse(operation, ToolOutput(code, FIXTURES / name))


def facts(result: object) -> list[tuple[str, str, object]]:
    assert isinstance(result, Parsed), result
    return [(d.measurement, d.target.id, d.value) for d in result.drafts]


# -- the shared promises ------------------------------------------------------------------------


@pytest.mark.parametrize("adapter", [TypeScriptAdapter(), ADAPTER])
def test_the_adapter_keeps_every_promise_of_the_interface(
    adapter: TypeScriptAdapter, project: Path, tmp_path: Path
) -> None:
    problems = language_problems(
        adapter, project, tmp_path / "scratch", test_id="src/calc.test.ts > calc > adds"
    )
    assert problems == []


# -- finding and classifying ----------------------------------------------------------------------


def test_a_project_is_found_by_its_manifest_and_its_tsconfig(project: Path, tmp_path: Path) -> None:
    found = ADAPTER.detect(project)

    assert found is not None
    assert (found.language, found.roots) == ("typescript", (".",))
    assert found.manifests == ("package.json", "tsconfig.json")
    assert found.toolchain_probe is not None
    assert found.toolchain_probe.plan.argv == ("n", "--version")
    (project / "tsconfig.json").unlink()
    assert ADAPTER.detect(project) is None
    assert ADAPTER.detect(tmp_path / "nowhere") is None


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("src/calc.ts", FileClass.PRODUCTION),
        ("src/view.tsx", FileClass.PRODUCTION),
        ("lib/util.mjs", FileClass.PRODUCTION),
        ("src/calc.test.ts", FileClass.TEST),
        ("src\\calc.spec.tsx", FileClass.TEST),
        ("src/__tests__/calc.ts", FileClass.TEST),
        ("src/__fixtures__/sample.ts", FileClass.FIXTURE),
        ("src/__mocks__/api.ts", FileClass.FIXTURE),
        ("node_modules/dep/index.js", None),
        ("dist/calc.js", None),
        ("eslint.config.js", None),
        ("vitest.config.ts", None),
        ("package.json", None),
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


def test_format_lists_what_is_not_in_prettiers_form_and_rewrites_only_when_asked(
    project: Path,
) -> None:
    script = "node_modules/prettier/bin/prettier.cjs"
    assert argv(Operation.FORMAT, project) == ("n", script, "-l", "--write", "--", ".")
    assert argv(Operation.FORMAT, project, files=("a.ts",), check_only=True) == (
        "n", script, "-l", "--", "a.ts",
    )  # fmt: skip


def test_lint_runs_eslint_with_the_formatter_that_prints_relative_paths(project: Path) -> None:
    script = "node_modules/eslint/bin/eslint.js"
    assert argv(Operation.LINT, project, files=("a.ts",)) == (
        "n", script, "-f", ESLINT_FORMATTER, "--", "a.ts",
    )  # fmt: skip
    assert argv(Operation.LINT_FIX, project) == (
        "n", script, "-f", ESLINT_FORMATTER, "--fix", "--", ".",
    )  # fmt: skip


def test_build_type_checks_the_project_without_emitting(project: Path) -> None:
    assert argv(Operation.BUILD, project) == (
        "n", "node_modules/typescript/bin/tsc", "--noEmit", "--pretty", "false", "-p", ".",
    )  # fmt: skip


def test_a_tool_the_project_does_not_depend_on_is_not_supported(tmp_path: Path) -> None:
    bare = make_project(tmp_path / "bare")
    (bare / "package.json").write_text('{"name": "bare"}', encoding="utf-8")

    for operation in (Operation.FORMAT, Operation.LINT, Operation.LINT_FIX, Operation.BUILD):
        assert isinstance(plan(operation, bare), NotSupported)
    for operation in (Operation.TEST, Operation.COVERAGE):
        assert isinstance(plan(operation, bare), NotSupported)
    assert isinstance(plan(Operation.TEST, tmp_path / "nowhere"), NotSupported)


def test_vitest_prints_tap_and_writes_an_istanbul_report_for_coverage(project: Path) -> None:
    run = ("n", "node_modules/vitest/vitest.mjs", "run", "--reporter=tap-flat")

    assert argv(Operation.TEST, project) == run
    assert argv(Operation.COVERAGE, project) == (
        *run, "--coverage.enabled", "--coverage.reporter=json",
        f"--coverage.reportsDirectory={COVERAGE_DIRECTORY}",
    )  # fmt: skip
    assert ADAPTER.report(Operation.COVERAGE) == ReportSpec(
        ReportFormat.ISTANBUL_JSON, COVERAGE_REPORT
    )
    assert isinstance(ADAPTER.report(Operation.MUTATION), NotSupported)


def test_jest_prints_tap_through_forjas_reporter_and_writes_an_istanbul_report(
    jest_project: Path,
) -> None:
    run = ("n", "node_modules/jest/bin/jest.js", f"--reporters={JEST_REPORTER}")

    assert argv(Operation.TEST, jest_project) == run
    assert argv(Operation.COVERAGE, jest_project) == (
        *run, "--coverage", "--coverageReporters=json",
        f"--coverageDirectory={COVERAGE_DIRECTORY}",
    )  # fmt: skip


def test_vitest_wins_when_a_project_depends_on_both(tmp_path: Path) -> None:
    both = make_project(tmp_path / "both", vitest="5", jest="30")

    assert "node_modules/vitest/vitest.mjs" in argv(Operation.TEST, both)


def test_no_filter_of_the_tests_by_file_or_for_coverage_is_offered(project: Path) -> None:
    for operation in (Operation.TEST, Operation.COVERAGE):
        assert isinstance(plan(operation, project, test_files=("src/a.test.ts",)), NotSupported)
    assert isinstance(
        plan(Operation.COVERAGE, project, tests=("src/a.test.ts > adds",)), NotSupported
    )


def test_node_may_read_its_own_settings_from_the_environment(project: Path) -> None:
    answer = plan(Operation.TEST, project)

    assert isinstance(answer, CommandPlan)
    assert {"LOCALAPPDATA", "NODE_PATH"} <= answer.env_allow
    assert "package.json" in answer.config_files


def test_every_tool_names_its_version_probe() -> None:
    info = ADAPTER.tool_info(Operation.LINT)

    assert isinstance(info, ToolInfo)
    assert info.tool_probe.plan.argv == ("n", "node_modules/eslint/bin/eslint.js", "--version")
    fmt = ADAPTER.tool_info(Operation.FORMAT)
    assert isinstance(fmt, ToolInfo)
    assert fmt.tool == "prettier"
    assert isinstance(ADAPTER.tool_info(Operation.MUTATION), NotSupported)


# -- reading what they print ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["vitest-pass.txt", "jest-pass.txt"])
def test_the_outcome_of_every_test_is_read_from_the_tap_stream(name: str) -> None:
    result = read(Operation.TEST, name, 0)

    assert facts(result) == [
        ("test.outcome", "src/calc.test.ts > add", "passed"),
        ("test.outcome", "src/calc.test.ts > group > later", "skipped"),
        ("test.outcome", "src/calc.test.ts > group > wrong", "passed"),
        ("test.skipped", "tests", 1),
    ]


@pytest.mark.parametrize("name", ["vitest-fail.txt", "jest-fail.txt"])
def test_a_failed_test_is_a_failed_outcome(name: str) -> None:
    result = read(Operation.TEST, name, 1)

    assert ("test.outcome", "src/calc.test.ts > group > wrong", "failed") in facts(result)


def test_a_test_file_that_does_not_load_is_one_failed_outcome_named_after_the_file() -> None:
    vitest = facts(read(Operation.TEST, "vitest-load.txt", 1))
    jest = facts(read(Operation.TEST, "jest-load.txt", 1))

    assert ("test.outcome", "src/broken.test.ts", "failed") in vitest
    assert jest == [("test.outcome", "src/calc.test.ts", "failed")]


def test_test_output_that_contradicts_the_exit_code_is_not_understood() -> None:
    assert isinstance(read(Operation.TEST, "vitest-fail.txt", 0), Unparseable)
    assert isinstance(read(Operation.TEST, "vitest-pass.txt", 1), Unparseable)
    assert isinstance(read(Operation.TEST, "vitest-pass.txt", 2), Unparseable)
    assert isinstance(read(Operation.TEST, "vitest-pass.txt", None), Unparseable)


def test_a_project_without_tests_collects_none(tmp_path: Path) -> None:
    log = tmp_path / "t.log"
    log.write_text("TAP version 13\n1..0\n", encoding="utf-8")

    assert facts(ADAPTER.parse(Operation.TEST, ToolOutput(0, log))) == [
        ("test.collected", "tests", 0)
    ]
    assert isinstance(ADAPTER.parse(Operation.TEST, ToolOutput(1, log)), Unparseable)


def test_build_errors_are_reported_per_file() -> None:
    assert facts(read(Operation.BUILD, "tsc.txt", 2)) == [("build.compile_error", "src/bad.ts", 1)]
    assert facts(read(Operation.BUILD, "tsc.txt", 0)) == []
    assert isinstance(read(Operation.BUILD, "tsc.txt", None), Unparseable)


def test_a_build_failure_without_a_file_is_one_failure_of_the_build(tmp_path: Path) -> None:
    log = tmp_path / "b.log"
    log.write_text("error TS5058: The specified path does not exist: 'x'.\n", encoding="utf-8")

    assert facts(ADAPTER.parse(Operation.BUILD, ToolOutput(1, log))) == [
        ("build.failed", "build", 1)
    ]


def test_prettier_lists_unformatted_files_and_files_it_cannot_parse() -> None:
    assert facts(read(Operation.FORMAT, "prettier.txt", 1)) == [
        ("format.unformatted", "src/ugly.ts", 1)
    ]
    assert facts(read(Operation.FORMAT, "prettier-syntax.txt", 2)) == [
        ("format.invalid_syntax", "src/syn.ts", 1)
    ]
    assert isinstance(read(Operation.FORMAT, "prettier.txt", 3), Unparseable)


def test_eslint_findings_are_counted_per_file_and_rule(tmp_path: Path) -> None:
    result = read(Operation.LINT, "eslint.txt", 1)

    assert facts(result) == [
        ("lint.@typescript-eslint/no-explicit-any", "src/lint.ts", 1),
        ("lint.@typescript-eslint/no-unused-vars", "src/lint.ts", 1),
        ("lint.prefer-const", "src/lint.ts", 1),
    ]
    empty = tmp_path / "none"
    assert facts(ADAPTER.parse(Operation.LINT, ToolOutput(0, empty))) == []
    assert isinstance(ADAPTER.parse(Operation.LINT, ToolOutput(1, empty)), Unparseable)
    assert isinstance(read(Operation.LINT, "eslint.txt", 2), Unparseable)


@pytest.mark.parametrize("name", ["vitest-coverage.json", "jest-coverage.json"])
def test_the_real_istanbul_report_names_the_lines_the_tests_ran(name: str) -> None:
    root = Path("/work/calc")
    text = (FIXTURES / name).read_text(encoding="utf-8")

    report = read_coverage(ReportFormat.ISTANBUL_JSON, text, root)

    calc = report.of("src/calc.ts")
    assert calc is not None
    assert 2 in calc.covered
    assert not calc.missed
