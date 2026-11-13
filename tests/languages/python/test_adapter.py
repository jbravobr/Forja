"""The Python adapter: the shared promises, the commands it plans, and what it reads back."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from forja.core.adapters import (
    CommandPlan,
    FileClass,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolInfo,
    ToolOutput,
    Unparseable,
    read_output_lines,
)
from forja.core.adapters.common import PROPERTY_SEED
from forja.core.evidence import TargetKind
from forja.core.reports import ReportFormat, ReportSpec
from forja.core.reports.readers import read_coverage
from forja.languages.python import PythonAdapter
from forja.languages.python.adapter import COVERAGE_DRIVER, COVERAGE_REPORT, default_cache_dir
from tests.core.adapters.conformance import language_problems

ADAPTER = PythonAdapter(
    python=("py",), ruff=("rf",), mypy=("mp",), cache_dir=Path("cache-for-tests")
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    return root


def log_with(tmp_path: Path, *lines: str, name: str = "op.log") -> Path:
    log = tmp_path / name
    log.write_text("\n".join(f"[out] {line}" for line in lines), encoding="utf-8")
    return log


def parsed(operation: Operation, tmp_path: Path, code: int | None, *lines: str) -> Parsed:
    result = ADAPTER.parse(operation, ToolOutput(code, log_with(tmp_path, *lines)))
    assert isinstance(result, Parsed), result
    return result


def facts(result: Parsed) -> list[tuple[str, str, object]]:
    return [(d.measurement, d.target.id, d.value) for d in result.drafts]


# -- the shared promises ------------------------------------------------------------------------


def test_the_adapter_keeps_every_promise_of_the_interface(project: Path, tmp_path: Path) -> None:
    assert language_problems(PythonAdapter(), project, tmp_path / "scratch") == []


# -- finding and classifying ----------------------------------------------------------------------


def test_a_project_is_found_by_its_manifest(project: Path, tmp_path: Path) -> None:
    found = ADAPTER.detect(project)

    assert found is not None
    assert (found.language, found.roots, found.manifests) == ("python", (".",), ("pyproject.toml",))
    assert found.toolchain_probe is not None and found.toolchain_probe.plan.argv == (
        "py",
        "--version",
    )
    assert ADAPTER.detect(tmp_path / "nowhere") is None


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("src/pkg/mod.py", FileClass.PRODUCTION),
        ("src/pkg/mod.pyi", FileClass.PRODUCTION),
        ("tests/test_mod.py", FileClass.TEST),
        ("tests\\unit\\helpers.py", FileClass.TEST),
        ("pkg/test_mod.py", FileClass.TEST),
        ("pkg/mod_test.py", FileClass.TEST),
        ("pkg/conftest.py", FileClass.TEST),
        ("tests/fixtures/sample.py", FileClass.FIXTURE),
        ("tests/testdata/sample.py", FileClass.FIXTURE),
        ("fixtures/sample.py", FileClass.PRODUCTION),
        ("README.md", None),
        ("pyproject.toml", None),
        ("docs/guide.rst", None),
    ],
)
def test_files_are_classified_by_where_they_live_and_how_they_are_named(
    path: str, kind: FileClass | None
) -> None:
    assert ADAPTER.classify(path) is kind


# -- commands --------------------------------------------------------------------------------------


def argv(operation: Operation, project: Path, **options: object) -> tuple[str, ...]:
    plan = ADAPTER.plan(operation, OperationRequest(root=project, **options))  # type: ignore[arg-type]
    assert isinstance(plan, CommandPlan)
    return plan.argv


def test_format_checks_or_rewrites_the_files_it_is_given(project: Path) -> None:
    assert argv(Operation.FORMAT, project) == (
        "rf", "format", "--no-cache", "--force-exclude", "--", ".",
    )  # fmt: skip
    assert argv(Operation.FORMAT, project, files=("a.py",), check_only=True) == (
        "rf", "format", "--check", "--no-cache", "--force-exclude", "--", "a.py",
    )  # fmt: skip


def test_lint_reads_the_concise_format_and_the_fix_only_touches_what_is_safe(project: Path) -> None:
    options = ("--output-format=concise", "--no-cache", "--force-exclude")

    assert argv(Operation.LINT, project, files=("a.py", "b.py")) == (
        "rf", "check", "--ignore-noqa", *options, "--", "a.py", "b.py",
    )  # fmt: skip
    assert argv(Operation.LINT_FIX, project) == (
        "rf",
        "check",
        "--fix",
        "--exit-zero",
        *options,
        "--",
        ".",
    )


def test_build_compiles_everything_but_other_peoples_code(project: Path) -> None:
    command = argv(Operation.BUILD, project)

    assert command[:5] == ("py", "-m", "compileall", "-q", "-x")
    assert command[6:] == ("--", ".")


def test_tests_run_in_the_repositorys_python_and_can_be_limited_to_files(project: Path) -> None:
    everything = argv(Operation.TEST, project)
    one_file = argv(Operation.TEST, project, test_files=("tests/test_a.py",))

    assert everything == (
        "py", "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rA", "--color=no",
    )  # fmt: skip
    assert one_file == (*everything, "--", "tests/test_a.py")


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("pyproject.toml", "[project]\nname = 'x'\ndependencies = ['Hypothesis>=6']\n"),
        ("requirements-dev.txt", "pytest\nhypothesis[pytest]==6.1\n"),
    ],
)
def test_the_seed_of_hypothesis_is_fixed_where_the_repository_declares_it(
    project: Path, name: str, text: str
) -> None:
    (project / name).write_text(text, encoding="utf-8")
    seed = f"--hypothesis-seed={PROPERTY_SEED}"

    assert argv(Operation.TEST, project)[-1] == seed
    assert argv(Operation.COVERAGE, project)[-1] == seed
    assert argv(Operation.TEST, project, test_files=("t.py",))[-3:] == (seed, "--", "t.py")
    assert argv(Operation.COVERAGE, project, test_files=("t.py",))[-3:] == (seed, "--", "t.py")


def test_the_seed_is_left_out_where_hypothesis_is_not_declared(project: Path) -> None:
    (project / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (project / "requirements-bad.txt").write_bytes(b"\xff\xfe")

    for operation in (Operation.TEST, Operation.COVERAGE):
        assert not any("hypothesis" in part for part in argv(operation, project))


def test_coverage_runs_pytest_under_the_driver_and_names_its_report(project: Path) -> None:
    everything = argv(Operation.COVERAGE, project)
    one_file = argv(Operation.COVERAGE, project, test_files=("tests/test_a.py",))

    assert everything[:2] == ("py", str(COVERAGE_DRIVER))
    assert everything[2:5] == ("--report", COVERAGE_REPORT, "--")
    assert everything[5:] == ("-p", "no:cacheprovider", "-q", "--color=no")
    assert one_file == (*everything, "--", "tests/test_a.py")
    assert ADAPTER.report(Operation.COVERAGE) == ReportSpec(
        ReportFormat.COVERAGE_PY_JSON, COVERAGE_REPORT
    )
    assert isinstance(ADAPTER.report(Operation.MUTATION), NotSupported)
    assert isinstance(
        ADAPTER.plan(Operation.COVERAGE, OperationRequest(root=project, tests=("a::b",))),
        NotSupported,
    )


def test_a_plan_runs_in_the_project_leaves_no_bytecode_behind_and_hashes_the_configuration(
    project: Path, tmp_path: Path
) -> None:
    plan = ADAPTER.plan(Operation.LINT, OperationRequest(root=project))

    assert isinstance(plan, CommandPlan)
    assert plan.cwd == project
    assert plan.env_set["NO_COLOR"] == "1"
    assert plan.env_set["PYTHONPYCACHEPREFIX"] == "cache-for-tests"
    assert "pyproject.toml" in plan.config_files and "ruff.toml" in plan.config_files


@pytest.mark.parametrize("operation", [Operation.MUTATION, Operation.E2E])
def test_what_the_adapter_does_not_offer_is_said_so(operation: Operation, project: Path) -> None:
    assert isinstance(ADAPTER.plan(operation, OperationRequest(root=project)), NotSupported)
    assert isinstance(ADAPTER.tool_info(operation), NotSupported)


def test_a_test_name_filter_is_refused_rather_than_ignored(project: Path) -> None:
    answer = ADAPTER.plan(Operation.TEST, OperationRequest(root=project, tests=("a::b",)))

    assert isinstance(answer, NotSupported)


def test_each_operation_names_its_tool_and_how_to_ask_its_version() -> None:
    expected = {
        Operation.FORMAT: ("ruff", ("rf", "--version")),
        Operation.LINT: ("ruff", ("rf", "--version")),
        Operation.LINT_FIX: ("ruff", ("rf", "--version")),
        Operation.TYPES: ("mypy", ("mp", "--version")),
        Operation.BUILD: ("compileall", ("py", "--version")),
        Operation.TEST: ("pytest", ("py", "-m", "pytest", "--version")),
        Operation.COVERAGE: ("coverage.py", ("py", "-m", "coverage", "--version")),
    }
    for operation, (tool, probe) in expected.items():
        info = ADAPTER.tool_info(operation)
        assert isinstance(info, ToolInfo)
        assert (info.tool, info.tool_probe.plan.argv) == (tool, probe)
        assert (info.toolchain, info.toolchain_probe.plan.argv) == ("python", ("py", "--version"))


def test_there_is_no_progress_to_show() -> None:
    assert all(ADAPTER.progress_parser(op) is None for op in Operation)


# -- reading lint ----------------------------------------------------------------------------------


def test_lint_counts_violations_per_file_and_rule(tmp_path: Path) -> None:
    result = parsed(
        Operation.LINT,
        tmp_path,
        1,
        r"pkg\bad.py:1:5: invalid-syntax: Expected an identifier",
        r"pkg\bad.py:2:1: invalid-syntax: unexpected EOF while parsing",
        r"pkg\m.py:1:8: F401 [*] `os` imported but unused",
        "pkg/m.py:9:1: F401 `sys` imported but unused",
        "pkg/m.py:12:5: E501 Line too long (120 > 100)",
        "Found 5 errors.",
        "[*] 1 fixable with `--fix`.",
    )

    assert facts(result) == [
        ("lint.invalid-syntax", "pkg/bad.py", 2),
        ("lint.E501", "pkg/m.py", 1),
        ("lint.F401", "pkg/m.py", 2),
    ]
    assert {d.target.kind for d in result.drafts} == {TargetKind.FILE}
    assert {d.unit for d in result.drafts} == {"violations"}


def test_a_clean_lint_has_nothing_to_report(tmp_path: Path) -> None:
    assert parsed(Operation.LINT, tmp_path, 0, "All checks passed!").drafts == ()
    assert (
        parsed(Operation.LINT_FIX, tmp_path, 0, "Found 2 errors (2 fixed, 0 remaining).").drafts
        == ()
    )


@pytest.mark.parametrize("code", [None, 2, 101])
def test_lint_that_did_not_finish_normally_is_not_read(tmp_path: Path, code: int | None) -> None:
    result = ADAPTER.parse(
        Operation.LINT, ToolOutput(code, log_with(tmp_path, "pkg/a.py:1:1: F401 x"))
    )

    assert isinstance(result, Unparseable)


def test_violations_nobody_can_read_are_not_taken_for_a_clean_run(tmp_path: Path) -> None:
    result = ADAPTER.parse(Operation.LINT, ToolOutput(1, log_with(tmp_path, "something changed")))

    assert isinstance(result, Unparseable)


# -- types -----------------------------------------------------------------------------------------


def test_types_run_mypy_on_the_files_with_the_repositorys_python_and_its_defaults_when_no_settings(
    project: Path,
) -> None:
    assert argv(Operation.TYPES, project, files=("a.py", "b.py")) == (
        "mp",
        "--python-executable", "py",
        "--config-file=", "--follow-imports=silent",
        "--output", "json", "--cache-dir", os.devnull,
        "--disable-error-code=import-not-found",
        "--disable-error-code=import-untyped",
        "--", "a.py", "b.py",
    )  # fmt: skip
    assert argv(Operation.TYPES, project)[-2:] == ("--", ".")


def error(path: str, code: str | None, severity: str = "error") -> str:
    return json.dumps(
        {
            "file": path,
            "line": 3,
            "column": 4,
            "message": "x: y",
            "code": code,
            "severity": severity,
        }
    )


def test_types_count_errors_per_file_and_code(tmp_path: Path) -> None:
    result = parsed(
        Operation.TYPES,
        tmp_path,
        1,
        error(r"pkg\m.py", "arg-type"),
        error("pkg/m.py", "arg-type"),
        error("pkg/m.py", "return-value"),
        error("pkg/n.py", "arg-type"),
        error("pkg/n.py", "note-only", severity="note"),
        "plain text that is not an error",
        "{not json",
        '["a list"]',
        '{"file": 3, "severity": "error"}',
    )

    assert facts(result) == [
        ("types.arg-type", "pkg/m.py", 2),
        ("types.return-value", "pkg/m.py", 1),
        ("types.arg-type", "pkg/n.py", 1),
    ]
    assert {d.target.kind for d in result.drafts} == {TargetKind.FILE}
    assert {d.unit for d in result.drafts} == {"errors"}


def test_an_error_without_a_code_is_still_counted(tmp_path: Path) -> None:
    result = parsed(Operation.TYPES, tmp_path, 1, error("a.py", None))

    assert facts(result) == [("types.error", "a.py", 1)]


def test_a_clean_types_run_has_nothing_to_report(tmp_path: Path) -> None:
    assert parsed(Operation.TYPES, tmp_path, 0).drafts == ()


def test_a_long_error_line_is_not_dropped(tmp_path: Path) -> None:
    line = json.dumps(
        {"file": "a.py", "message": "m" * 5000, "code": "arg-type", "severity": "error"}
    )

    assert facts(parsed(Operation.TYPES, tmp_path, 1, line)) == [("types.arg-type", "a.py", 1)]


@pytest.mark.parametrize("code", [None, 2, 101])
def test_types_that_did_not_finish_normally_are_not_read(tmp_path: Path, code: int | None) -> None:
    result = ADAPTER.parse(
        Operation.TYPES, ToolOutput(code, log_with(tmp_path, error("a.py", "x")))
    )

    assert isinstance(result, Unparseable)


def test_errors_nobody_can_read_are_not_taken_for_a_clean_run(tmp_path: Path) -> None:
    result = ADAPTER.parse(Operation.TYPES, ToolOutput(1, log_with(tmp_path, "something changed")))

    assert isinstance(result, Unparseable)


# -- reading format --------------------------------------------------------------------------------


def test_format_names_the_files_that_would_change_and_the_ones_it_cannot_read(
    tmp_path: Path,
) -> None:
    result = parsed(
        Operation.FORMAT,
        tmp_path,
        2,
        "invalid-syntax: Expected an identifier",
        r" --> pkg\bad.py:1:5",
        "  |",
        "unformatted: File would be reformatted",
        r" --> pkg\m.py:4:7",
        "  |",
        "unformatted: File would be reformatted",
        " --> pkg/a.py:2:1",
        "1 file would be reformatted",
    )

    assert facts(result) == [
        ("format.invalid_syntax", "pkg/bad.py", 1),
        ("format.unformatted", "pkg/a.py", 1),
        ("format.unformatted", "pkg/m.py", 1),
    ]


def test_the_older_wording_of_ruff_is_read_too(tmp_path: Path) -> None:
    result = parsed(
        Operation.FORMAT, tmp_path, 1, "Would reformat: pkg/m.py", "1 file would be reformatted"
    )

    assert facts(result) == [("format.unformatted", "pkg/m.py", 1)]


def test_a_formatted_tree_has_nothing_to_report(tmp_path: Path) -> None:
    assert parsed(Operation.FORMAT, tmp_path, 0, "2 files already formatted").drafts == ()


@pytest.mark.parametrize(("code", "line"), [(None, ""), (3, ""), (1, "no idea"), (2, "no idea")])
def test_format_output_that_cannot_be_read_is_unparseable(
    tmp_path: Path, code: int | None, line: str
) -> None:
    result = ADAPTER.parse(Operation.FORMAT, ToolOutput(code, log_with(tmp_path, line)))

    assert isinstance(result, Unparseable)


# -- reading build ---------------------------------------------------------------------------------


def test_build_names_the_files_that_do_not_compile(tmp_path: Path) -> None:
    result = parsed(
        Operation.BUILD,
        tmp_path,
        1,
        "*** Error compiling 'pkg\\\\bad.py'...",
        '  File "pkg\\bad.py", line 1',
        "SyntaxError: invalid syntax",
        "*** Error compiling './pkg/worse.py'...",
        "*** Error compiling 'pkg\\\\bad.py'...",
    )

    assert facts(result) == [
        ("build.compile_error", "pkg/bad.py", 1),
        ("build.compile_error", "pkg/worse.py", 1),
    ]


def test_a_build_that_failed_without_saying_where_still_failed(tmp_path: Path) -> None:
    assert facts(parsed(Operation.BUILD, tmp_path, 1, "boom")) == [("build.failed", "build", 1)]


def test_a_build_that_compiled_has_nothing_to_report(tmp_path: Path) -> None:
    assert parsed(Operation.BUILD, tmp_path, 0).drafts == ()
    assert isinstance(
        ADAPTER.parse(Operation.BUILD, ToolOutput(None, log_with(tmp_path))), Unparseable
    )


# -- reading tests ---------------------------------------------------------------------------------


def test_every_test_has_an_outcome_and_skips_are_counted(tmp_path: Path) -> None:
    result = parsed(
        Operation.TEST,
        tmp_path,
        1,
        "=========================== short test summary info ===========================",
        "PASSED tests/test_m.py::test_ok",
        "PASSED tests/test_m.py::test_p[a b]",
        "SKIPPED [1] tests\\test_m.py:13: not today",
        "SKIPPED [2] tests\\test_m.py:20: not today",
        "FAILED tests/test_m.py::test_bad - assert 1 == 2",
        "ERROR tests/test_m.py::test_err - RuntimeError: x - y",
        "XFAIL tests/test_m.py::test_known",
        "1 failed, 2 passed, 3 skipped in 0.18s",
    )

    assert facts(result) == [
        ("test.outcome", "tests/test_m.py::test_bad", "failed"),
        ("test.outcome", "tests/test_m.py::test_err", "error"),
        ("test.outcome", "tests/test_m.py::test_known", "xfail"),
        ("test.outcome", "tests/test_m.py::test_ok", "passed"),
        ("test.outcome", "tests/test_m.py::test_p[a b]", "passed"),
        ("test.skipped", "tests", 3),
    ]


def test_a_failed_test_says_what_it_raised_when_pytest_says_it(tmp_path: Path) -> None:
    result = parsed(
        Operation.TEST,
        tmp_path,
        1,
        "=========================== short test summary info ===========================",
        "FAILED tests/test_m.py::test_name - NameError: name 'page' is not defined",
        "FAILED tests/test_m.py::test_own - sample.errors.Missing: no page",
        "FAILED tests/test_m.py::test_bare - AssertionError",
        "FAILED tests/test_m.py::test_assert - assert 1 == 2",
        "FAILED tests/test_m.py::test_silent",
        "ERROR tests/test_m.py::test_err - NameError: name 'x' is not defined",
        "5 failed, 1 error in 0.18s",
    )

    assert [fact for fact in facts(result) if fact[0] == "test.failure"] == [
        ("test.failure", "tests/test_m.py::test_bare", "AssertionError"),
        ("test.failure", "tests/test_m.py::test_name", "NameError"),
        ("test.failure", "tests/test_m.py::test_own", "sample.errors.Missing"),
    ]


def test_a_collection_error_is_an_error_of_the_file(tmp_path: Path) -> None:
    result = parsed(Operation.TEST, tmp_path, 2, "ERROR tests/test_imp.py", "1 error in 0.17s")

    assert facts(result) == [("test.outcome", "tests/test_imp.py", "error")]


def test_no_tests_collected_is_a_result_not_a_failure_to_read(tmp_path: Path) -> None:
    assert facts(parsed(Operation.TEST, tmp_path, 5, "no tests ran in 0.01s")) == [
        ("test.collected", "tests", 0)
    ]


@pytest.mark.parametrize(
    ("code", "lines"),
    [
        (None, ()),
        (3, ("INTERNALERROR",)),
        (4, ("usage: pytest",)),
        (1, ("PASSED tests/a.py::t",)),
        (0, ("FAILED tests/a.py::t - x",)),
    ],
)
def test_test_output_that_does_not_add_up_is_not_trusted(
    tmp_path: Path, code: int | None, lines: tuple[str, ...]
) -> None:
    result = ADAPTER.parse(Operation.TEST, ToolOutput(code, log_with(tmp_path, *lines)))

    assert isinstance(result, Unparseable)


def test_an_operation_nobody_reads_is_unparseable(tmp_path: Path) -> None:
    assert isinstance(
        ADAPTER.parse(Operation.MUTATION, ToolOutput(0, log_with(tmp_path))), Unparseable
    )


def test_absurdly_long_lines_are_not_ours(tmp_path: Path) -> None:
    huge = "a.py:1:1: F401 " + "x" * 5000

    assert parsed(Operation.LINT, tmp_path, 0, huge).drafts == ()


# -- the real tools --------------------------------------------------------------------------------


REAL = PythonAdapter()


def run(operation: Operation, root: Path, tmp_path: Path, **options: object) -> Parsed:
    plan = REAL.plan(operation, OperationRequest(root=root, **options))  # type: ignore[arg-type]
    assert isinstance(plan, CommandPlan)
    done = subprocess.run(plan.argv, cwd=root, capture_output=True, text=True, check=False)
    log = tmp_path / f"{operation.value}.log"
    log.write_text(
        "".join(f"[out] {line}\n" for line in (done.stdout + done.stderr).splitlines()),
        encoding="utf-8",
    )
    result = REAL.parse(operation, ToolOutput(done.returncode, log))
    assert isinstance(result, Parsed), (result, read_output_lines(log))
    return result


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    root = tmp_path / "sample"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\n[tool.ruff]\nline-length = 100\n', encoding="utf-8"
    )
    (root / "pkg" / "m.py").write_text("import os\n\n\ndef g( x ):\n  return x\n", encoding="utf-8")
    (root / "tests" / "test_m.py").write_text(
        "import pytest\n\n\ndef test_ok():\n    assert True\n\n\n"
        "@pytest.mark.parametrize('n', [1, 2])\ndef test_p(n):\n    assert n\n\n\n"
        "def test_bad():\n    assert 1 == 2\n\n\ndef test_skip():\n    pytest.skip('x')\n",
        encoding="utf-8",
    )
    return root


def test_the_real_ruff_and_the_adapter_agree_on_lint_and_format(
    sample: Path, tmp_path: Path
) -> None:
    lint = run(Operation.LINT, sample, tmp_path, files=("pkg",))
    layout = run(Operation.FORMAT, sample, tmp_path, files=("pkg",), check_only=True)

    assert facts(lint) == [("lint.F401", "pkg/m.py", 1)]
    assert facts(layout) == [("format.unformatted", "pkg/m.py", 1)]


def test_the_real_compileall_names_the_file_it_cannot_compile(sample: Path, tmp_path: Path) -> None:
    assert run(Operation.BUILD, sample, tmp_path, files=("pkg",)).drafts == ()
    (sample / "pkg" / "bad.py").write_text("def (:\n", encoding="utf-8")

    assert facts(run(Operation.BUILD, sample, tmp_path, files=("pkg",))) == [
        ("build.compile_error", "pkg/bad.py", 1)
    ]


def test_the_real_pytest_outcomes_are_read_test_by_test(sample: Path, tmp_path: Path) -> None:
    result = run(Operation.TEST, sample, tmp_path, test_files=("tests",))

    assert facts(result) == [
        ("test.outcome", "tests/test_m.py::test_bad", "failed"),
        ("test.outcome", "tests/test_m.py::test_ok", "passed"),
        ("test.outcome", "tests/test_m.py::test_p[1]", "passed"),
        ("test.outcome", "tests/test_m.py::test_p[2]", "passed"),
        ("test.skipped", "tests", 1),
    ]


def test_the_real_coverage_driver_writes_a_report_the_reader_understands(
    sample: Path, tmp_path: Path
) -> None:
    plan = REAL.plan(Operation.COVERAGE, OperationRequest(root=sample, test_files=("tests",)))
    assert isinstance(plan, CommandPlan)
    (sample / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (sample / "tests" / "test_m.py").write_text(
        "from pkg.m import g\n\n\ndef test_g():\n    assert g(1) == 1\n", encoding="utf-8"
    )

    done = subprocess.run(plan.argv, cwd=sample, capture_output=True, text=True, check=False)
    spec = REAL.report(Operation.COVERAGE)
    assert isinstance(spec, ReportSpec)
    report = read_coverage(spec.format, (sample / spec.path).read_text(encoding="utf-8"), sample)

    assert done.returncode == 0, done.stdout + done.stderr
    module = report.of("pkg/m.py")
    assert module is not None
    assert module.covered == frozenset({1, 4, 5}) and not module.missed  # import, def, return
    assert module.tests[5] == frozenset({"tests/test_m.py::test_g"})
    assert not (sample / ".coverage").exists()  # recorded in memory: nothing else is left behind


def test_the_real_tools_report_their_versions(sample: Path) -> None:
    for operation, word in ((Operation.LINT, "ruff"), (Operation.TEST, "pytest")):
        info = REAL.tool_info(operation)
        assert isinstance(info, ToolInfo)
        done = subprocess.run(
            info.tool_probe.plan.argv, cwd=sample, capture_output=True, text=True, check=False
        )
        assert word in (done.stdout + done.stderr).lower()
    assert sys.executable in REAL.plan(Operation.TEST, OperationRequest(root=sample)).argv  # type: ignore[union-attr]


# -- what a path may not do ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operation",
    [Operation.FORMAT, Operation.LINT, Operation.LINT_FIX, Operation.TYPES, Operation.BUILD],
)
def test_a_path_that_looks_like_an_option_stays_a_path(operation: Operation, project: Path) -> None:
    command = argv(operation, project, files=("--config=evil.toml",))

    assert command[-2:] == ("--", "--config=evil.toml")


def test_test_files_come_after_the_end_of_options(project: Path) -> None:
    command = argv(Operation.TEST, project, test_files=("-pevil",))

    assert command[-2:] == ("--", "-pevil")


def test_compiled_files_go_to_the_users_own_cache_unless_told_otherwise(project: Path) -> None:
    plan = PythonAdapter().plan(Operation.BUILD, OperationRequest(root=project))

    assert isinstance(plan, CommandPlan)
    cache = Path(plan.env_set["PYTHONPYCACHEPREFIX"])
    assert cache == default_cache_dir() and cache.parts[-2:] == ("forja", "pycache")
    assert not cache.is_relative_to(Path(tempfile.gettempdir()))


def test_what_a_test_prints_before_the_summary_is_not_a_verdict(tmp_path: Path) -> None:
    result = parsed(
        Operation.TEST,
        tmp_path,
        0,
        "----- Captured stdout call -----",
        "FAILED tests/a.py::forged - pretending",
        "PASSED tests/b.py::forged",
        "=========== short test summary info ===========",
        "PASSED tests/a.py::t",
        "5 passed in 0.5s",
    )

    assert facts(result) == [("test.outcome", "tests/a.py::t", "passed")]


def test_a_summary_header_printed_by_a_test_does_not_hide_the_real_one(tmp_path: Path) -> None:
    result = parsed(
        Operation.TEST,
        tmp_path,
        1,
        "=========== short test summary info ===========",
        "PASSED tests/a.py::forged",
        "=========== short test summary info ===========",
        "FAILED tests/a.py::real - boom",
        "1 failed in 0.5s",
    )

    assert facts(result) == [("test.outcome", "tests/a.py::real", "failed")]


def test_tests_of_a_src_layout_copy_import_the_copy_and_not_an_editable_install(
    project: Path,
) -> None:
    (project / "src").mkdir()

    for operation in (Operation.TEST, Operation.COVERAGE):
        plan = ADAPTER.plan(operation, OperationRequest(root=project))
        assert isinstance(plan, CommandPlan)
        assert plan.env_set["PYTHONPATH"] == str(project / "src")

    lint = ADAPTER.plan(Operation.LINT, OperationRequest(root=project))
    assert isinstance(lint, CommandPlan)
    assert "PYTHONPATH" not in lint.env_set


def test_tests_of_a_flat_layout_set_no_python_path(project: Path) -> None:
    plan = ADAPTER.plan(Operation.TEST, OperationRequest(root=project))

    assert isinstance(plan, CommandPlan)
    assert "PYTHONPATH" not in plan.env_set
