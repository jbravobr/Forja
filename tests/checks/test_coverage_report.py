"""A tool may name its own report directory (a run id), so the report is a pattern, and several
reports (one per test project) become one."""

import asyncio
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from forja.checks.coverage import CoverageShare, merge_coverage, report_paths, run_line_tests
from forja.checks.tooling import Execution
from forja.core.adapters import CommandPlan, NotSupported, Operation, OperationRequest
from forja.core.adapters.scoped import ScopedAdapter
from forja.core.gates import GateInput
from forja.core.process import Outcome, ProcessResult
from forja.core.reports import ReportFormat, ReportSpec
from forja.core.reports.models import CoverageReport, FileCoverage
from tests.core.adapters.fakes import FakeLanguageAdapter


def covering(covered: set[int], missed: set[int], tests: dict[int, set[str]] | None = None):  # type: ignore[no-untyped-def]
    return FileCoverage(
        covered=frozenset(covered),
        missed=frozenset(missed),
        tests={line: frozenset(names) for line, names in (tests or {}).items()},
    )


def test_a_plain_path_is_the_one_report(tmp_path: Path) -> None:
    assert report_paths(tmp_path, ".cover/report.xml") == [tmp_path / ".cover/report.xml"]


def test_a_pattern_finds_the_report_of_every_run_directory(tmp_path: Path) -> None:
    for run in ("b", "a"):
        (tmp_path / ".cover" / run).mkdir(parents=True)
        (tmp_path / ".cover" / run / "report.xml").write_text("x", encoding="utf-8")
    (tmp_path / ".cover" / "a" / "other.txt").write_text("x", encoding="utf-8")

    assert report_paths(tmp_path, ".cover/*/report.xml") == [
        tmp_path / ".cover/a/report.xml",
        tmp_path / ".cover/b/report.xml",
    ]
    assert report_paths(tmp_path, ".nothing/*/report.xml") == []


def test_one_report_is_returned_as_it_is() -> None:
    only = CoverageReport({"a.cs": covering({1}, {2})})

    assert merge_coverage([only]) is only


def test_a_line_is_covered_if_any_report_covers_it_and_the_tests_add_up() -> None:
    first = CoverageReport(
        {"a.cs": covering({1}, {2, 3}, {1: {"t1"}}), "b.cs": covering({5}, set())}
    )
    second = CoverageReport({"a.cs": covering({2}, {1, 3}, {2: {"t2"}})})

    merged = merge_coverage([first, second])

    a = merged.of("a.cs")
    assert a is not None
    assert (a.covered, a.missed) == (frozenset({1, 2}), frozenset({3}))
    assert a.tests == {1: frozenset({"t1"}), 2: frozenset({"t2"})}
    assert merged.of("b.cs") is not None


def link_directory(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:  # Windows without the right to make links: a junction needs none
        done = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=False
        )
        if done.returncode != 0:
            pytest.skip("this machine cannot make links")


def test_a_report_behind_a_linked_directory_is_not_taken(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "report.xml").write_text("x", encoding="utf-8")
    work = tmp_path / "work"
    (work / ".cover" / "real").mkdir(parents=True)
    (work / ".cover" / "real" / "report.xml").write_text("x", encoding="utf-8")
    link_directory(work / ".cover" / "linked", outside)

    assert report_paths(work, ".cover/*/report.xml") == [work / ".cover/real/report.xml"]


# -- the tests of each line --------------------------------------------------------------------

DATA = GateInput(Path("."), "c" * 40, "t", "s", "a")
LINES = ".forja-test-lines"


class _Tools:
    """Runs nothing: ``leaves`` writes what the tool would have written, and the run ends as
    told."""

    def __init__(
        self,
        leaves: Callable[[CommandPlan], None] = lambda plan: None,
        outcome: Outcome = Outcome.EXITED,
        code: int | None = 0,
    ) -> None:
        self.leaves, self.outcome, self.code = leaves, outcome, code
        self.ran: list[tuple[str, CommandPlan]] = []

    async def run(self, check: str, plan: CommandPlan) -> Execution:
        self.ran.append((check, plan))
        self.leaves(plan)
        result = ProcessResult(self.outcome, self.code, 0.0, plan.cwd / "no.log")
        return Execution(check, plan, "run-1", result)


class _Lines(FakeLanguageAdapter):
    """An adapter that finds the tests of each line in a run of its own."""

    def __init__(self, spec: ReportSpec | NotSupported, planned: bool = True) -> None:
        self.spec, self.planned = spec, planned
        self.asked: list[tuple[Operation, OperationRequest]] = []

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan | NotSupported:
        self.asked.append((operation, request))
        if operation is Operation.TEST_LINES and self.planned:
            return CommandPlan(("tool", "lines"), request.root)
        return NotSupported(operation.value, "not here")

    def report(self, operation: Operation) -> ReportSpec | NotSupported:
        return self.spec if operation is Operation.TEST_LINES else NotSupported(operation.value)


ONE_FILE = ReportSpec(ReportFormat.TEST_LINES_JSON, f"{LINES}/lines.json")
EACH_TEST = ReportSpec(ReportFormat.GO_COVERPROFILE, f"{LINES}/*.out", index=f"{LINES}/index.json")


def _measured() -> CoverageShare:
    return CoverageShare(
        CoverageReport({"calc.go": covering({3, 4}, {7}), "b.go": covering({1}, set())})
    )


def _lines(
    tools: _Tools, adapter: object, root: Path, share: CoverageShare | None
) -> CoverageReport:
    return asyncio.run(run_line_tests(tools, adapter, root, DATA, share))  # type: ignore[arg-type]


def _one_file(text: str) -> Callable[[CommandPlan], None]:
    def leaves(plan: CommandPlan) -> None:
        (plan.cwd / LINES).mkdir(parents=True, exist_ok=True)
        (plan.cwd / LINES / "lines.json").write_text(text, encoding="utf-8")
        (plan.cwd / LINES / "calc.test.exe").write_bytes(b"binary")  # whatever else it needed

    return leaves


def test_a_report_that_names_tests_is_all_there_is_to_know(tmp_path: Path) -> None:
    named = CoverageReport({"a.py": covering({1}, set(), {1: {"t"}})})
    tools, adapter = _Tools(), _Lines(ONE_FILE)

    assert _lines(tools, adapter, tmp_path, CoverageShare(named)) is named
    assert tools.ran == [] and adapter.asked == []


def test_the_tests_of_each_line_come_from_a_run_of_the_adapter_and_join_the_coverage(
    tmp_path: Path,
) -> None:
    (tmp_path / "calc.go").write_text("x\n", encoding="utf-8")
    tools = _Tools(_one_file('{"calc.go": {"3": ["p/TestA"], "7": ["p/TestB"]}}'))
    adapter, share = _Lines(ONE_FILE), _measured()

    report = _lines(tools, adapter, tmp_path, share)

    calc = report.of("calc.go")
    assert calc is not None
    assert calc.tests == {3: frozenset({"p/TestA"})}  # line 7 is not covered in the report
    assert (calc.covered, calc.missed) == (frozenset({3, 4}), frozenset({7}))
    assert [check for check, _ in tools.ran] == ["test_lines"]
    assert adapter.asked == [(Operation.TEST_LINES, OperationRequest(root=tmp_path))]
    assert share.report is report and share.line_tests is True
    # nothing the run left stays in the worktree
    assert not (tmp_path / LINES).exists()


def test_the_tests_of_each_line_are_asked_for_once_whatever_came_of_it(tmp_path: Path) -> None:
    tools, adapter, share = _Tools(_one_file("{}")), _Lines(ONE_FILE), _measured()

    first = _lines(tools, adapter, tmp_path, share)
    second = _lines(tools, adapter, tmp_path, share)

    assert first == second == _measured().report  # the run named no test
    assert len(tools.ran) == 1


def test_without_a_share_the_tests_of_each_line_are_still_found(tmp_path: Path) -> None:
    class Covering(_Lines):
        def plan(
            self, operation: Operation, request: OperationRequest
        ) -> CommandPlan | NotSupported:
            if operation is Operation.COVERAGE:
                return CommandPlan(("tool", "cover"), request.root)
            return super().plan(operation, request)

        def report(self, operation: Operation) -> ReportSpec | NotSupported:
            if operation is Operation.COVERAGE:
                return ReportSpec(ReportFormat.GO_COVERPROFILE, "cover.out")
            return super().report(operation)

    def leaves(plan: CommandPlan) -> None:
        if plan.argv[1] == "cover":
            (plan.cwd / "cover.out").write_text("mode: set\ncalc.go:3.1,3.2 1 1\n", "utf-8")
        else:
            _one_file('{"calc.go": {"3": ["p/TestA"]}}')(plan)

    (tmp_path / "calc.go").write_text("x\n", encoding="utf-8")

    report = _lines(_Tools(leaves), Covering(ONE_FILE), tmp_path, None)

    assert report.tests_of("calc.go", (3,)) == frozenset({"p/TestA"})


def test_a_coverage_report_for_each_test_is_read_with_its_index(tmp_path: Path) -> None:
    (tmp_path / "calc.go").write_text("x\n", encoding="utf-8")

    def leaves(plan: CommandPlan) -> None:
        folder = plan.cwd / LINES
        folder.mkdir()
        (folder / "0.out").write_text("mode: set\nm/calc.go:3.1,3.2 1 1\n", encoding="utf-8")
        (folder / "1.out").write_text("mode: set\nm/calc.go:3.1,4.2 1 1\n", encoding="utf-8")
        (folder / "index.json").write_text('{"0.out": "m/TestA", "1.out": "m/TestB"}', "utf-8")

    report = _lines(_Tools(leaves), _Lines(EACH_TEST), tmp_path, _measured())

    calc = report.of("calc.go")
    assert calc is not None
    assert calc.tests == {3: frozenset({"m/TestA", "m/TestB"}), 4: frozenset({"m/TestB"})}
    assert not (tmp_path / LINES).exists()


@pytest.mark.parametrize(
    "adapter",
    [
        _Lines(NotSupported("test_lines")),  # says no report
        _Lines(ONE_FILE, planned=False),  # has no command
        object(),  # has neither
    ],
)
def test_an_adapter_with_no_run_for_the_tests_of_each_line_leaves_the_coverage_as_it_was(
    tmp_path: Path, adapter: object
) -> None:
    if not isinstance(adapter, _Lines):

        class Bare:
            def plan(self, operation: Operation, request: OperationRequest) -> NotSupported:
                return NotSupported(operation.value)

        adapter = Bare()
    tools, share = _Tools(), _measured()

    assert _lines(tools, adapter, tmp_path, share) == _measured().report
    assert tools.ran == []
    assert share.line_tests is True


@pytest.mark.parametrize(
    ("outcome", "code", "text"),
    [
        (Outcome.EXITED, 1, '{"calc.go": {"3": ["p/TestA"]}}'),  # the run failed
        (Outcome.TIMED_OUT, None, '{"calc.go": {"3": ["p/TestA"]}}'),  # or did not end
        (Outcome.EXITED, 0, "not json"),  # or left what cannot be read
        (Outcome.EXITED, 0, '{"../calc.go": {"3": ["p/TestA"]}}'),
    ],
)
def test_a_run_of_the_tests_of_each_line_that_fails_changes_no_verdict_and_leaves_nothing(
    tmp_path: Path, outcome: Outcome, code: int | None, text: str
) -> None:
    (tmp_path / "calc.go").write_text("x\n", encoding="utf-8")
    tools, share = _Tools(_one_file(text), outcome, code), _measured()

    assert _lines(tools, _Lines(ONE_FILE), tmp_path, share) == _measured().report
    assert share.line_tests is True
    assert not (tmp_path / LINES).exists()


def test_a_run_that_wrote_no_report_leaves_the_coverage_as_it_was(tmp_path: Path) -> None:
    def no_index(plan: CommandPlan) -> None:
        (plan.cwd / LINES).mkdir()
        (plan.cwd / LINES / "0.out").write_text("mode: set\n", encoding="utf-8")

    for tools, spec in ((_Tools(), ONE_FILE), (_Tools(no_index), EACH_TEST)):
        assert _lines(tools, _Lines(spec), tmp_path, _measured()) == _measured().report
        assert not (tmp_path / LINES).exists()


def test_in_a_project_below_the_root_the_reports_are_read_from_it_and_named_by_the_repository(
    tmp_path: Path,
) -> None:
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "calc.go").write_text("x\n", encoding="utf-8")
    tools = _Tools(_one_file('{"calc.go": {"3": ["p/TestA"]}}'))
    inner = _Lines(ONE_FILE)
    share = CoverageShare(CoverageReport({"svc/calc.go": covering({3}, set())}))

    report = _lines(tools, ScopedAdapter(inner, "svc"), tmp_path, share)

    assert report.tests_of("svc/calc.go", (3,)) == frozenset({"p/TestA"})
    assert tools.ran[0][1].cwd == tmp_path / "svc"
    assert inner.asked == [(Operation.TEST_LINES, OperationRequest(root=tmp_path / "svc"))]
    assert not (tmp_path / "svc" / LINES).exists()


def test_a_report_of_the_tests_of_each_line_that_is_not_text_changes_nothing(
    tmp_path: Path,
) -> None:
    def leaves(plan: CommandPlan) -> None:
        (plan.cwd / LINES).mkdir()
        (plan.cwd / LINES / "lines.json").write_bytes(b"\xff\xfe\x00 not utf-8")

    assert _lines(_Tools(leaves), _Lines(ONE_FILE), tmp_path, _measured()) == _measured().report
    assert not (tmp_path / LINES).exists()


def test_a_run_of_the_tests_of_each_line_that_is_stopped_still_leaves_nothing(
    tmp_path: Path,
) -> None:
    class Stopped(_Tools):
        async def run(self, check: str, plan: CommandPlan) -> Execution:
            _one_file("{}")(plan)
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        _lines(Stopped(), _Lines(ONE_FILE), tmp_path, _measured())

    assert not (tmp_path / LINES).exists()
