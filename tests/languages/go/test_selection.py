"""Which tests a Go plan asks for: the filter, the stop at the first failure and the line tests."""

from pathlib import Path

import pytest

from forja.core.adapters import (
    CommandPlan,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
)
from forja.core.reports import TEST_LINES_DIRECTORY, ReportFormat, ReportSpec
from forja.languages.go import GoAdapter
from forja.languages.go.adapter import LINE_DRIVER, LINE_INDEX
from forja.languages.go.selection import module_of, quote, selection

ADAPTER = GoAdapter(go=("g",), gofmt=("gf",), python=("py",))
MODULE = "example.com/calc"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "go.mod").write_text(f"module {MODULE}\n\ngo 1.22\n", encoding="utf-8")
    return root


def plan(project: Path, operation: Operation, **options: object) -> CommandPlan | NotSupported:
    answer = ADAPTER.plan(operation, OperationRequest(root=project, **options))  # type: ignore[arg-type]
    return answer


def argv(project: Path, operation: Operation, **options: object) -> tuple[str, ...]:
    answer = plan(project, operation, **options)
    assert isinstance(answer, CommandPlan), answer
    return answer.argv


# -- the ids of a filtered run -------------------------------------------------------------------


def test_the_ids_become_one_run_pattern_per_level_and_their_packages_in_their_order() -> None:
    ids = (f"{MODULE}/b/TestX/one", f"{MODULE}/a/TestY", f"{MODULE}/b/TestX/two")

    packages, pattern = selection(ids, MODULE) or ((), "")

    assert packages == ("./b", "./a")
    assert pattern == "^(TestX|TestY)$/^(one|two)$"


def test_the_root_package_is_the_dot_and_names_are_quoted_as_regexp_quotemeta_does() -> None:
    assert selection((f"{MODULE}/TestA.b+c",), MODULE) == ((".",), r"^(TestA\.b\+c)$")
    assert quote("a(b)[c]") == r"a\(b\)\[c\]"


def test_an_id_outside_the_module_or_without_a_test_name_is_not_placed() -> None:
    assert selection(("other.org/x/TestA",), MODULE) is None
    # nor one whose folder would leave the module
    assert selection((f"{MODULE}/../../x/TestA",), MODULE) is None
    assert selection((f"{MODULE}/./TestA",), MODULE) is None
    assert selection((f"{MODULE}//TestA",), MODULE) is None
    assert selection((f"{MODULE}/pkg/helper",), MODULE) is None


def test_the_module_is_read_from_the_manifest() -> None:
    assert module_of('// c\nmodule "example.com/x"\n') == "example.com/x"
    assert module_of("go 1.22\n") is None


# -- the commands ----------------------------------------------------------------------------------


def test_a_filtered_run_is_serial_and_stops_at_the_first_failing_test(project: Path) -> None:
    ids = (f"{MODULE}/b/TestX/one", f"{MODULE}/a/TestY")

    assert argv(project, Operation.TEST, tests=ids, rerun=True) == (
        "g", "test", "-count=1", "-json", "-failfast", "-p", "1",
        "-run", "^(TestX|TestY)$/^(one)$", "./b", "./a",
    )  # fmt: skip


def test_without_a_rerun_the_filtered_run_is_serial_and_does_not_stop(project: Path) -> None:
    ids = (f"{MODULE}/a/TestY",)

    assert argv(project, Operation.TEST, tests=ids) == (
        "g", "test", "-count=1", "-json", "-p", "1", "-run", "^(TestY)$", "./a",
    )  # fmt: skip


def test_a_filtered_run_too_long_for_one_command_is_not_supported(project: Path) -> None:
    ids = tuple(f"{MODULE}/a/TestT{i}/{'s' * 500}{i}" for i in range(80))

    assert isinstance(plan(project, Operation.TEST, tests=ids, rerun=True), NotSupported)


def test_a_test_outside_the_module_is_not_supported(project: Path) -> None:
    assert isinstance(plan(project, Operation.TEST, tests=("other.org/x/TestA",)), NotSupported)


def test_the_whole_run_stops_at_the_first_failure_only_on_a_rerun(project: Path) -> None:
    assert argv(project, Operation.TEST, rerun=True) == (
        "g", "test", "-count=1", "-json", "-failfast", "./...",
    )  # fmt: skip
    assert argv(project, Operation.TEST) == ("g", "test", "-count=1", "-json", "./...")


def test_a_filter_by_file_is_still_not_offered(project: Path) -> None:
    assert isinstance(plan(project, Operation.TEST, test_files=("a_test.go",)), NotSupported)


# -- the line tests --------------------------------------------------------------------------------


def test_the_line_tests_run_the_forja_piece_with_the_interpreter_and_the_go_tool(
    project: Path,
) -> None:
    answer = plan(project, Operation.TEST_LINES)

    assert isinstance(answer, CommandPlan)
    assert answer.argv == ("py", str(LINE_DRIVER), TEST_LINES_DIRECTORY, "g")
    assert answer.cwd == project
    assert "GOCACHE" in answer.env_allow


def test_the_line_tests_are_reported_as_one_profile_per_test_with_an_index(project: Path) -> None:
    assert ADAPTER.report(Operation.TEST_LINES) == ReportSpec(
        ReportFormat.GO_COVERPROFILE, f"{TEST_LINES_DIRECTORY}/*.out", index=LINE_INDEX
    )


def test_a_filtered_run_where_a_test_fails_is_read_as_that_failure(tmp_path: Path) -> None:
    log = tmp_path / "out.log"
    lines = [
        '{"Action":"run","Package":"example.com/calc/a","Test":"TestY"}',
        '{"Action":"fail","Package":"example.com/calc/a","Test":"TestY","Elapsed":0.1}',
        '{"Action":"fail","Package":"example.com/calc/a","Elapsed":0.2}',
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    found = ADAPTER.parse(Operation.TEST, ToolOutput(1, log))

    assert isinstance(found, Parsed), found
    outcomes = {d.target.id: d.value for d in found.drafts if d.measurement == "test.outcome"}
    assert outcomes == {"example.com/calc/a/TestY": "failed"}
