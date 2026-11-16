"""The filter by test, the stop at the first failing test, and the tests of each line: the commands
the adapter plans for Vitest and Jest, and the output of a filtered run that failed."""

import re
from pathlib import Path

import pytest

from forja.checks.mutation import failed_test
from forja.core.adapters import (
    CommandPlan,
    LanguageCapability,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
)
from forja.core.reports import ReportFormat, ReportSpec
from forja.languages.typescript.adapter import (
    JEST_LINES_REPORTER,
    JEST_LINES_SETUP,
    JEST_REPORTER,
    JEST_SEQUENCER,
    TEST_LINES_REPORT,
    VITEST_CONFIG,
    VITEST_LINES,
    _pattern,
)
from tests.languages.typescript.test_adapter import ADAPTER, FIXTURES, make_project

VITEST_RUN = ("n", "node_modules/vitest/vitest.mjs", "run")
JEST_RUN = ("n", "node_modules/jest/bin/jest.js")
ASKED = (
    "test/a.test.ts > add > adds",
    "test/b.test.ts > sign is one for positives",
)


def plan_of(root: Path, **options: object) -> CommandPlan | NotSupported:
    return ADAPTER.plan(Operation.TEST, OperationRequest(root=root, **options))  # type: ignore[arg-type]


def test_vitest_runs_the_asked_files_in_series_with_one_name_pattern(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p", vitest="5")

    answer = plan_of(project, tests=ASKED, rerun=True)

    assert isinstance(answer, CommandPlan)
    assert answer.argv == (
        *VITEST_RUN,
        "test/a.test.ts",
        "test/b.test.ts",
        "-t",
        r"^(?:add\s+>\s+adds|sign\s+is\s+one\s+for\s+positives)$",
        "--reporter=tap-flat",
        "--no-file-parallelism",
        f"--config={VITEST_CONFIG}",
        "--bail=1",
    )
    assert answer.env_set["FORJA_FILE_ORDER"] == "test/a.test.ts,test/b.test.ts"


def test_the_stop_at_the_first_failure_is_only_asked_for_a_rerun(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p", vitest="5")

    answer = plan_of(project, tests=ASKED)

    assert isinstance(answer, CommandPlan)
    assert "--bail=1" not in answer.argv
    assert "--no-file-parallelism" in answer.argv


def test_jest_runs_the_asked_files_in_series_in_the_order_given(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p", jest="30")

    answer = plan_of(project, tests=ASKED, rerun=True)

    assert isinstance(answer, CommandPlan)
    assert answer.argv == (
        *JEST_RUN,
        "test/a.test.ts",
        "test/b.test.ts",
        f"--reporters={JEST_REPORTER}",
        "-t",
        "^(?:add\\s+adds|sign\\s+is\\s+one\\s+for\\s+positives)$",
        "-i",
        f"--testSequencer={JEST_SEQUENCER}",
        "--bail",
    )
    assert answer.env_set["FORJA_FILE_ORDER"] == "test/a.test.ts,test/b.test.ts"


def test_a_test_id_without_its_file_is_not_filtered(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p", vitest="5")

    assert isinstance(plan_of(project, tests=("adds",)), NotSupported)


@pytest.mark.parametrize("runner", ["vitest", "jest"])
def test_a_test_file_named_like_an_option_is_not_filtered(tmp_path: Path, runner: str) -> None:
    project = make_project(tmp_path / "p", **{runner: "5"})

    answer = plan_of(project, tests=("--outputFile=x.test.ts > adds",))

    assert answer == NotSupported("test", "a test file named like an option")


def test_a_filter_that_does_not_fit_the_command_line_is_not_supported(tmp_path: Path) -> None:
    project = make_project(tmp_path / "p", vitest="5")
    long_name = "x" * 9000

    assert isinstance(plan_of(project, tests=(f"test/a.test.ts > {long_name}",)), NotSupported)


def test_the_whole_command_stops_at_the_first_failure_only_when_rerun(tmp_path: Path) -> None:
    vitest = make_project(tmp_path / "v", vitest="5")
    jest = make_project(tmp_path / "j", jest="30")

    assert plan_of(vitest, rerun=True).argv == (  # type: ignore[union-attr]
        *VITEST_RUN, "--reporter=tap-flat", "--bail=1",
    )  # fmt: skip
    assert plan_of(vitest).argv == (*VITEST_RUN, "--reporter=tap-flat")  # type: ignore[union-attr]
    assert plan_of(jest, rerun=True).argv == (  # type: ignore[union-attr]
        *JEST_RUN, f"--reporters={JEST_REPORTER}", "--bail",
    )  # fmt: skip


def test_the_tests_of_each_line_run_once_with_the_pieces_of_the_forja(tmp_path: Path) -> None:
    vitest = make_project(tmp_path / "v", vitest="5")
    jest = make_project(tmp_path / "j", jest="30")

    lines = ADAPTER.plan(Operation.TEST_LINES, OperationRequest(root=vitest))
    jest_lines = ADAPTER.plan(Operation.TEST_LINES, OperationRequest(root=jest))

    assert isinstance(lines, CommandPlan) and lines.argv == ("n", VITEST_LINES)
    assert isinstance(jest_lines, CommandPlan)
    assert jest_lines.argv == (
        *JEST_RUN,
        "--coverage",
        "--coverageReporters=json",
        "--coverageDirectory=.forja-test-lines/cobertura",
        f"--setupFilesAfterEnv={JEST_LINES_SETUP}",
        f"--reporters={JEST_LINES_REPORTER}",
    )


def test_the_tests_of_each_line_are_read_from_the_forja_format() -> None:
    assert ADAPTER.report(Operation.TEST_LINES) == ReportSpec(
        ReportFormat.TEST_LINES_JSON, TEST_LINES_REPORT
    )


def test_the_adapter_declares_the_filter_and_the_lines() -> None:
    assert LanguageCapability.TEST_FILTER_BY_TEST in ADAPTER.capabilities
    assert LanguageCapability.TEST_LINES in ADAPTER.capabilities


def test_a_project_without_a_runner_has_no_tests_of_each_line(tmp_path: Path) -> None:
    bare = make_project(tmp_path / "bare")
    (bare / "package.json").write_text('{"name": "x"}', encoding="utf-8")

    assert isinstance(ADAPTER.plan(Operation.TEST_LINES, OperationRequest(root=bare)), NotSupported)


def test_the_name_pattern_matches_a_name_whose_spaces_differ() -> None:
    jest = re.compile(_pattern(("add > adds (1) [x.y]",), jest=True))
    vitest = re.compile(_pattern(("add > adds (1) [x.y]",), jest=False))

    assert jest.match("add  adds (1) [x.y]")
    assert vitest.match("add > adds (1) [x.y]")
    assert not jest.match("add adds (1) [xzy]")


@pytest.mark.parametrize("name", ["vitest-bail.txt", "jest-bail.txt"])
def test_the_test_that_failed_in_a_filtered_run_is_found(name: str) -> None:
    output = ToolOutput(1, FIXTURES / name)

    assert isinstance(ADAPTER.parse(Operation.TEST, output), Parsed)
    assert failed_test(ADAPTER, output, ASKED) == "test/b.test.ts > sign is one for positives"
