"""The C# adapter's commands for the tests named (filter, order, stop) and for the tests of each
line: the commands it plans, the ids it reads back, and what it promises when the tool is absent."""

import os
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
from forja.core.adapters.common import fits_command_line
from forja.core.reports import ReportFormat, ReportSpec
from forja.core.reports.formats import TEST_LINES_DIRECTORY
from forja.languages.csharp import CSharpAdapter
from forja.languages.csharp.adapter import INJECTION, filter_of, find_dotnet_coverage

COVERAGE_TOOL = ("C:/tools/dotnet-coverage.exe",)
ADAPTER = CSharpAdapter(dotnet=("d",), dotnet_coverage=COVERAGE_TOOL)
WITHOUT_TOOL = CSharpAdapter(dotnet=("d",))
ROOT = Path("/work/calc")
FACT = "Calc.Tests.CalculatorTests.Adds"
CASE = 'Calc.Tests.CalculatorTests.Sum(a: 1, b: "x|y&z=(1)~!")'


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "calc"
    root.mkdir()
    (root / "Calc.sln").write_text("", encoding="utf-8")
    return root


def run_plan(
    adapter: CSharpAdapter, operation: Operation, root: Path, **request: object
) -> CommandPlan | NotSupported:
    return adapter.plan(operation, OperationRequest(root=root, **request))  # type: ignore[arg-type]


def test_the_ids_are_escaped_for_the_filter_and_joined_by_or() -> None:
    expected = (
        r"DisplayName=Calc.Tests.CalculatorTests.Sum\(a: 1, b: \"x\|y\&z\=\(1\)\~\!\"\)"
        + r"|DisplayName=Calc.Tests.CalculatorTests.Adds"
    )

    assert filter_of((CASE, FACT)) == expected


def test_a_plain_test_run_is_the_same_as_before(project: Path) -> None:
    answer = run_plan(ADAPTER, Operation.TEST, project)

    assert isinstance(answer, CommandPlan)
    assert answer.argv == (
        "d",
        "test",
        "Calc.sln",
        "--nologo",
        "--logger",
        "console;verbosity=normal",
    )


def test_the_whole_run_with_rerun_only_stops_at_the_first_failure(project: Path) -> None:
    answer = run_plan(ADAPTER, Operation.TEST, project, rerun=True)

    assert isinstance(answer, CommandPlan)
    assert answer.argv[-1] == "-p:ForjaRunner=runner-stop.json"
    assert "--filter" not in answer.argv
    assert f"-p:CustomAfterMicrosoftCommonTargets={INJECTION / 'forja.targets'}" in answer.argv
    assert "-p:ForjaOrder=true" not in answer.argv


def test_the_named_tests_run_serially_stopping_at_the_first_failure_in_their_order(
    project: Path,
) -> None:
    answer = run_plan(ADAPTER, Operation.TEST, project, tests=(CASE, FACT), rerun=True)

    assert isinstance(answer, CommandPlan)
    assert answer.argv[answer.argv.index("--filter") + 1] == filter_of((CASE, FACT))
    assert "-p:ForjaRunner=runner-serial-stop.json" in answer.argv
    assert "-p:ForjaOrder=true" in answer.argv
    assert answer.env_set["FORJA_ORDER"] == f"{CASE}\n{FACT}"


def test_the_named_tests_that_do_not_fit_in_one_command_are_not_supported(project: Path) -> None:
    ids = tuple(f"Calc.Tests.Big.Case_{n:05d}(value: {'x' * 60})" for n in range(400))

    answer = run_plan(ADAPTER, Operation.TEST, project, tests=ids)

    assert isinstance(answer, NotSupported)


def test_the_filter_expression_is_an_argument_the_safety_check_accepts(project: Path) -> None:
    answer = run_plan(ADAPTER, Operation.TEST, project, tests=(CASE, FACT))

    assert isinstance(answer, CommandPlan)
    assert fits_command_line(answer.argv)


def test_the_tests_of_each_line_run_one_snapshot_per_test(project: Path) -> None:
    answer = run_plan(ADAPTER, Operation.TEST_LINES, project)

    assert isinstance(answer, CommandPlan)
    assert answer.argv[:1] == COVERAGE_TOOL
    assert answer.argv[1] == "collect"
    start = answer.argv.index("d")
    assert answer.argv[start : start + 3] == ("d", "test", "Calc.sln")
    assert "-p:ForjaSnap=true" in answer.argv
    assert "-m:1" in answer.argv  # one test project at a time: one index, one session
    assert answer.env_set["FORJA_DCT"] == COVERAGE_TOOL[0]
    assert answer.env_set["FORJA_DCT_HEAD"] == ""  # the command itself: nothing before its own
    assert answer.env_set["FORJA_SNAP"] == str(project / TEST_LINES_DIRECTORY)
    assert answer.env_set["FORJA_SESSION"].startswith("forja-")


def test_the_session_is_the_same_for_a_worktree_and_differs_between_worktrees(
    project: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "Calc.sln").write_text("", encoding="utf-8")

    first = run_plan(ADAPTER, Operation.TEST_LINES, project)
    again = run_plan(ADAPTER, Operation.TEST_LINES, project)
    second = run_plan(ADAPTER, Operation.TEST_LINES, other)

    assert isinstance(first, CommandPlan) and isinstance(again, CommandPlan)
    assert isinstance(second, CommandPlan)
    assert first.env_set["FORJA_SESSION"] == again.env_set["FORJA_SESSION"]
    assert first.env_set["FORJA_SESSION"] != second.env_set["FORJA_SESSION"]


def test_the_tests_of_each_line_are_not_offered_without_the_tool(project: Path) -> None:
    assert isinstance(run_plan(WITHOUT_TOOL, Operation.TEST_LINES, project), NotSupported)
    assert isinstance(WITHOUT_TOOL.report(Operation.TEST_LINES), NotSupported)
    assert "test_lines" not in {c.value for c in WITHOUT_TOOL.capabilities}
    assert "test_lines" in {c.value for c in ADAPTER.capabilities}


def test_the_tests_of_each_line_are_read_from_one_cobertura_report_per_test() -> None:
    assert ADAPTER.report(Operation.TEST_LINES) == ReportSpec(
        ReportFormat.COBERTURA_XML,
        f"{TEST_LINES_DIRECTORY}/*.xml",
        index=f"{TEST_LINES_DIRECTORY}/index.json",
    )


def test_a_failed_test_is_read_by_the_id_the_filter_takes(tmp_path: Path) -> None:
    log = tmp_path / "log.txt"
    log.write_text(
        f"  Passed {FACT} [1 ms]\n  Failed {CASE} [2 ms]\n  Error Message:\n   Assert\n",
        encoding="utf-8",
    )

    parsed = ADAPTER.parse(Operation.TEST, ToolOutput(1, log, ROOT))

    assert isinstance(parsed, Parsed)
    outcomes = {d.target.id: d.value for d in parsed.drafts if d.measurement == "test.outcome"}
    assert outcomes == {FACT: "passed", CASE: "failed"}


def test_dotnet_coverage_as_an_assembly_is_run_by_dotnet_in_the_run_and_in_each_snapshot(
    project: Path,
) -> None:
    assembly = ("dotnet", "C:/forja tools/dotnet-coverage.dll")
    adapter = CSharpAdapter(dotnet=("d",), dotnet_coverage=assembly)

    answer = run_plan(adapter, Operation.TEST_LINES, project)

    assert isinstance(answer, CommandPlan)
    assert answer.argv[:3] == (*assembly, "collect")
    assert answer.env_set["FORJA_DCT"] == "dotnet"
    # quoted: the piece in the test process hands it to the program as one argument
    assert answer.env_set["FORJA_DCT_HEAD"] == '"C:/forja tools/dotnet-coverage.dll"'


def test_dotnet_coverage_is_found_as_the_command_or_as_the_assembly_the_forja_installs(
    tmp_path: Path,
) -> None:
    command, installed, nothing = tmp_path / "user", tmp_path / "forja", tmp_path / "empty"
    for folder in (command, installed, nothing):
        folder.mkdir()
    name = "dotnet-coverage.exe" if os.name == "nt" else "dotnet-coverage"
    (command / name).write_bytes(b"")
    (command / name).chmod(0o755)
    (installed / "dotnet-coverage.dll").write_bytes(b"")

    def found(*folders: Path) -> tuple[str, ...] | None:
        return find_dotnet_coverage({"PATH": os.pathsep.join(str(folder) for folder in folders)})

    assert found(nothing) is None
    assert found(nothing, installed) == ("dotnet", str(installed / "dotnet-coverage.dll"))
    # the command, wherever it is on the PATH, before an assembly
    assert found(installed, command) == (str(command / name),)
    assert found() is None
    assert find_dotnet_coverage({"PATH": f"relative{os.pathsep}"}) is None
