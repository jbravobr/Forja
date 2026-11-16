"""Edges of the Python adapter: the words it uses, the units it reports, and where it looks."""

import sys
from pathlib import Path

import pytest

from forja.core.adapters import (
    EvidenceDraft,
    FileClass,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
    Unparseable,
)
from forja.core.evidence import Target, TargetKind
from forja.languages.python import PythonAdapter
from forja.languages.python import adapter as adapter_module
from forja.languages.python.adapter import default_cache_dir
from forja.languages.python.parsers import MAX_LINE

ADAPTER = PythonAdapter(python=("py",), ruff=("rf",), cache_dir=Path("cache"))


def read(tmp_path: Path, operation: Operation, code: int | None, *lines: str) -> object:
    log = tmp_path / "op.log"
    log.write_text("\n".join(f"[out] {line}" for line in lines), encoding="utf-8")
    return ADAPTER.parse(operation, ToolOutput(code, log))


def draft(
    measurement: str, kind: TargetKind, target: str, value: object, unit: str
) -> EvidenceDraft:
    return EvidenceDraft(measurement, Target(kind=kind, id=target), value, unit)  # type: ignore[arg-type]


# -- where compiled files go -----------------------------------------------------------------------


def test_compiled_files_go_to_the_local_app_data_of_a_windows_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(Path("C:/Users/x/AppData/Local")))

    assert default_cache_dir() == Path("C:/Users/x/AppData/Local") / "forja" / "pycache"


def test_a_windows_user_without_local_app_data_gets_the_home_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert default_cache_dir() == Path.home() / ".cache" / "forja" / "pycache"


def test_anyone_else_gets_the_home_cache_whatever_the_environment_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LOCALAPPDATA", "/somewhere/else")

    assert default_cache_dir() == Path.home() / ".cache" / "forja" / "pycache"
    assert vars(adapter_module)["sys"] is sys


# -- finding and describing ------------------------------------------------------------------------


def test_a_stub_test_file_is_a_test(tmp_path: Path) -> None:
    assert ADAPTER.classify("pkg/mod_test.pyi") is FileClass.TEST
    assert ADAPTER.classify("pkg/mod_test.py") is FileClass.TEST


def test_the_toolchain_probe_runs_where_the_project_is(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    found = ADAPTER.detect(tmp_path)

    assert found is not None and found.toolchain_probe is not None
    assert found.toolchain_probe.plan.cwd == tmp_path


def test_the_version_probes_run_wherever_the_program_is_found() -> None:
    for operation in (Operation.FORMAT, Operation.BUILD, Operation.TEST):
        info = ADAPTER.tool_info(operation)
        assert not isinstance(info, NotSupported)
        assert info.tool_probe.plan.cwd == info.toolchain_probe.plan.cwd == Path(".")


def test_what_is_not_offered_is_said_in_the_same_words_everywhere(tmp_path: Path) -> None:
    refusal = NotSupported("mutation", "the Python adapter does not offer it")

    assert ADAPTER.tool_info(Operation.MUTATION) == refusal
    assert ADAPTER.plan(Operation.MUTATION, OperationRequest(root=tmp_path)) == refusal
    assert ADAPTER.plan(Operation.COVERAGE, OperationRequest(root=tmp_path, tests=("a",))) == (
        NotSupported("coverage", "filtering by test name is not offered")
    )


def test_output_of_an_operation_nobody_reads_is_not_understood(tmp_path: Path) -> None:
    assert read(tmp_path, Operation.COVERAGE, 0) == Unparseable(
        "the Python adapter does not read coverage output"
    )


# -- what the parsers say when they cannot read ----------------------------------------------------


def test_the_reasons_for_not_reading_name_the_tool_and_what_it_did(tmp_path: Path) -> None:
    assert read(tmp_path, Operation.LINT, 2) == Unparseable("ruff check ended with 2")
    assert read(tmp_path, Operation.LINT, 1, "huh") == Unparseable(
        "ruff check reported violations in a form this parser does not know"
    )
    assert read(tmp_path, Operation.FORMAT, 5) == Unparseable("ruff format ended with 5")
    assert read(tmp_path, Operation.FORMAT, 1, "huh") == Unparseable(
        "ruff format failed in a form this parser does not know"
    )
    assert read(tmp_path, Operation.BUILD, None) == Unparseable("compileall did not finish")
    assert read(tmp_path, Operation.TEST, 4) == Unparseable("pytest ended with 4")
    assert read(tmp_path, Operation.TEST, 1, "PASSED a::t") == Unparseable(
        "pytest reported a failure in a form this parser does not know"
    )
    assert read(tmp_path, Operation.TEST, 0, "FAILED a::t") == Unparseable(
        "pytest passed but its output lists failures"
    )


# -- what the parsers report -----------------------------------------------------------------------


def test_every_draft_says_what_it_counts(tmp_path: Path) -> None:
    lint = read(tmp_path, Operation.LINT, 1, "a.py:1:1: F401 x")
    layout = read(
        tmp_path, Operation.FORMAT, 1, "unformatted: File would be reformatted", " --> a.py:1:1"
    )
    build = read(tmp_path, Operation.BUILD, 1, "*** Error compiling 'a.py'...")
    broken = read(tmp_path, Operation.BUILD, 1, "boom")
    tests = read(tmp_path, Operation.TEST, 1, "FAILED a::t - x", "SKIPPED [2] a.py:1: no")
    none = read(tmp_path, Operation.TEST, 5, "no tests ran")

    assert lint == Parsed((draft("lint.F401", TargetKind.FILE, "a.py", 1, "violations"),))
    assert layout == Parsed((draft("format.unformatted", TargetKind.FILE, "a.py", 1, "files"),))
    assert build == Parsed((draft("build.compile_error", TargetKind.FILE, "a.py", 1, "errors"),))
    assert broken == Parsed((draft("build.failed", TargetKind.MODULE, "build", 1, "failures"),))
    assert tests == Parsed(
        (
            draft("test.outcome", TargetKind.SYMBOL, "a::t", "failed", "outcome"),
            draft("test.skipped", TargetKind.MODULE, "tests", 2, "tests"),
        )
    )
    assert none == Parsed((draft("test.collected", TargetKind.MODULE, "tests", 0, "tests"),))


def test_a_line_as_long_as_the_limit_is_read_and_one_longer_is_not(tmp_path: Path) -> None:
    prefix = "a.py:1:1: F401 "
    exact = prefix + "x" * (MAX_LINE - len(prefix))

    assert len(exact) == MAX_LINE
    assert read(tmp_path, Operation.LINT, 1, exact) == Parsed(
        (draft("lint.F401", TargetKind.FILE, "a.py", 1, "violations"),)
    )
    assert isinstance(read(tmp_path, Operation.LINT, 1, exact + "x"), Unparseable)


def test_a_path_arrow_belongs_to_the_header_before_it_and_to_nothing_else(tmp_path: Path) -> None:
    result = read(
        tmp_path,
        Operation.FORMAT,
        1,
        " --> early.py:1:1",
        "unformatted: File would be reformatted",
        "some line between",
        " --> p.py:1:1",
        " --> stray.py:1:1",
    )

    assert result == Parsed((draft("format.unformatted", TargetKind.FILE, "p.py", 1, "files"),))
