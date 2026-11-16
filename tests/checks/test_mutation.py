"""The mutation check: pure pieces first, then the real thing over a small project."""

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from forja.checks.base import Undecided
from forja.checks.changes import Changes
from forja.checks.mutation import (
    DEFAULT_MIN_SCORE,
    AdapterRunner,
    Killer,
    MutationCheck,
    build_mutants,
    changed_lines,
    failed_test,
    in_order,
    judged_first,
    killers_of,
    load_killers,
    min_score,
    mutant_id,
    of_language,
    save_killers,
    untested_lines,
    worker_count,
)
from forja.core.adapters import (
    CommandPlan,
    EvidenceDraft,
    NotSupported,
    Operation,
    OperationRequest,
    Parsed,
    ToolOutput,
    Unparseable,
)
from forja.core.adapters.scoped import ScopedAdapter
from forja.core.evidence import Target, TargetKind
from forja.core.gates import GateInput
from forja.core.gates.models import (
    Approved,
    FindingKind,
    Inconclusive,
    InconclusiveReason,
    Rejected,
)
from forja.core.process import ProcessRunner, ProcessSpec
from forja.core.reports.models import CoverageReport, FileCoverage
from forja.core.states import Level
from forja.mutation.executor import Mutant, TestsDidNotRun, TestVerdict
from forja.mutation.sites import discover
from tests.checks.support import Scene, sinks
from tests.core.idempotency.conftest import Repo

pytest.importorskip("coverage")


def gate_input(**values: object) -> GateInput:
    return GateInput(Path("."), "c" * 40, "t", "s", "a", values=values)  # type: ignore[arg-type]


def test_the_default_limit_is_80_percent_70_for_python_and_go_and_the_policy_may_change_it() -> (
    None
):
    assert min_score(gate_input(), "java") == DEFAULT_MIN_SCORE == 0.80
    assert min_score(gate_input(), "python") == min_score(gate_input(), "go") == 0.70
    assert min_score(gate_input(thresholds={"python": {"min_score": 0.9}}), "python") == 0.9
    assert min_score(gate_input(thresholds={"python": {"min_score": 7}}), "python") == 0.70
    assert min_score(gate_input(thresholds={"java": {"min_score": True}}), "java") == 0.80
    # the ends of the range: all of them killed is a limit, none of them is not
    assert min_score(gate_input(thresholds={"java": {"min_score": 1}}), "java") == 1.0
    assert min_score(gate_input(thresholds={"java": {"min_score": 0}}), "java") == 0.80


def test_workers_come_from_the_policy_only_when_positive() -> None:
    assert worker_count(gate_input()) is None
    assert worker_count(gate_input(limits={"workers": 2})) == 2
    assert worker_count(gate_input(limits={"workers": 1})) == 1
    assert worker_count(gate_input(limits={"workers": 0})) is None


def test_the_changed_lines_are_those_of_the_files_asked_for_and_no_other() -> None:
    patch = (
        "diff --git a/m.py b/m.py",
        "--- a/m.py",
        "+++ b/m.py",
        "@@ -1,0 +2 @@",
        "+added",
        "diff --git a/other.py b/other.py",
        "--- a/other.py",
        "+++ b/other.py",
        "@@ -0,0 +1 @@",
        "+elsewhere",
    )
    changes = Changes((), 2, patch, ())

    assert changed_lines(changes, ("m.py",)) == {"m.py": frozenset({2})}


def test_only_the_lines_a_file_gained_are_mutated(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(a, b):\n    x = a + b\n    return a - b\n")
    coverage = CoverageReport({"m.py": FileCoverage(covered=frozenset({2, 3}), tests={})})

    mutants = build_mutants(tmp_path, {"m.py": frozenset({3})}, coverage, whole_functions=False)

    assert [(m.line, m.change) for m in mutants] == [(3, "- -> +")]
    assert mutants[0].covered


def test_a_changed_line_no_test_runs_gives_an_uncovered_mutant(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(a, b):\n    return a - b\n")
    covered = CoverageReport({"m.py": FileCoverage(covered=frozenset({1}), tests={})})

    for report in (covered, CoverageReport({})):
        (mutant,) = build_mutants(tmp_path, {"m.py": frozenset({2})}, report)
        assert not mutant.covered


@pytest.mark.parametrize(
    ("covered", "runs"),
    [
        ({2}, True),  # the report marks only the line the statement starts on
        ({4}, True),  # or the line of the site itself
        ({2, 4}, True),
        ({1, 3, 5}, False),
        (set(), False),
    ],
)
def test_a_site_counts_as_covered_by_its_own_line_or_the_line_its_statement_starts_on(
    tmp_path: Path, covered: set[int], runs: bool
) -> None:
    (tmp_path / "m.py").write_text("def f(a, b):\n    return (\n        a\n        - b\n    )\n")
    coverage = CoverageReport({"m.py": FileCoverage(covered=frozenset(covered), tests={})})

    (mutant,) = build_mutants(tmp_path, {"m.py": frozenset({4})}, coverage)

    assert (mutant.line, mutant.change) == (4, "- -> +")
    assert mutant.covered is runs


def test_a_file_the_rules_cannot_read_leaves_the_measurement_undecided(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(a, b):\n    return a - b\n")
    (tmp_path / "broken.py").write_text("def (:\n")
    coverage = CoverageReport({})

    with pytest.raises(Undecided) as stop:
        build_mutants(tmp_path, {"broken.py": frozenset({1}), "m.py": frozenset({2})}, coverage)

    result = stop.value.result
    assert isinstance(result, Inconclusive)
    assert result.reason is InconclusiveReason.UNSUPPORTED_CONSTRUCT
    assert "broken.py" in result.detail


def test_each_mutant_asks_for_the_tests_as_a_run_repeated_in_the_same_copy(
    tmp_path: Path,
) -> None:
    asked: list[tuple[Operation, OperationRequest]] = []

    class Recording:
        def plan(self, operation: Operation, request: OperationRequest) -> NotSupported:
            asked.append((operation, request))
            return NotSupported(operation.value, "only listening")

    runner = AdapterRunner(Recording(), {}, tmp_path / "logs")  # type: ignore[arg-type]

    with pytest.raises(TestsDidNotRun, match="only listening"):
        asyncio.run(runner(tmp_path / "copy", 5.0))

    assert asked == [(Operation.TEST, OperationRequest(root=tmp_path / "copy", rerun=True))]


class _ByPackage:
    """An adapter that, as the Go one, has a command for the folder of the file a mutant changed;
    with ``whole`` it has only the one command."""

    def __init__(self, whole: bool = False) -> None:
        self.whole = whole
        self.asked: list[OperationRequest] = []

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan:
        self.asked.append(request)
        folder = "..." if self.whole or request.mutated is None else Path(request.mutated).parent
        return CommandPlan(("tool", "test", f"./{folder}"), request.root)


def _mutant(file: str) -> Mutant:
    return Mutant(file, 1, "change", "f", b"", True)


def test_the_runner_of_a_file_tells_the_adapter_which_file_the_mutant_changed(
    tmp_path: Path,
) -> None:
    adapter = _ByPackage()
    whole = AdapterRunner(adapter, {}, tmp_path / "logs")  # type: ignore[arg-type]
    (mutant,) = judged_first(whole, tmp_path, [_mutant("a/x.go")])
    adapter.asked.clear()

    assert isinstance(mutant.first, AdapterRunner)
    assert mutant.first.command(tmp_path / "copy") == ("tool", "test", "./a")
    assert whole.command(tmp_path / "copy") == ("tool", "test", "./...")
    assert adapter.asked == [
        OperationRequest(root=tmp_path / "copy", rerun=True, mutated="a/x.go"),
        OperationRequest(root=tmp_path / "copy", rerun=True),
    ]


def test_mutants_whose_files_get_the_same_smaller_command_share_its_runner(tmp_path: Path) -> None:
    whole = AdapterRunner(_ByPackage(), {}, tmp_path / "logs")  # type: ignore[arg-type]
    mutants = [_mutant("a/x.go"), _mutant("b/y.go"), _mutant("a/z.go")]

    first, second, third = judged_first(whole, tmp_path, mutants)

    assert first.first is third.first  # one runner, so one baseline, for the folder
    assert second.first is not None and second.first is not first.first
    # the logs of every runner are numbered by the one counter, in the one folder
    assert isinstance(first.first, AdapterRunner)
    assert (first.first.ids, first.first.logs) == (whole.ids, whole.logs)
    assert [(m.file, m.line, m.change, m.mutated) for m in (first, second, third)] == [
        (m.file, m.line, m.change, m.mutated) for m in mutants
    ]


def test_where_the_adapter_has_only_the_whole_command_no_mutant_is_judged_first(
    tmp_path: Path,
) -> None:
    whole = AdapterRunner(_ByPackage(whole=True), {}, tmp_path / "logs")  # type: ignore[arg-type]
    mutants = [_mutant("a/x.go"), _mutant("b/y.go")]

    assert judged_first(whole, tmp_path, mutants) == mutants
    assert all(mutant.first is None for mutant in mutants)


def test_an_adapter_that_cannot_plan_the_tests_gives_no_first_runner(tmp_path: Path) -> None:
    class Refusing:
        def plan(self, operation: Operation, request: OperationRequest) -> NotSupported:
            return NotSupported(operation.value, "no tests here")

    whole = AdapterRunner(Refusing(), {}, tmp_path / "logs")  # type: ignore[arg-type]
    mutants = [_mutant("a/x.go")]

    assert whole.command(tmp_path) is None
    assert judged_first(whole, tmp_path, mutants) == mutants  # the run itself says why


def test_the_tests_of_a_mutant_run_at_low_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs: list[ProcessSpec] = []
    run = ProcessRunner.run

    async def noted(self: ProcessRunner, operation_id: str, spec: ProcessSpec, log: Path) -> object:
        specs.append(spec)
        return await run(self, operation_id, spec, log)

    monkeypatch.setattr(ProcessRunner, "run", noted)

    class Passing:
        def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan:
            return CommandPlan((sys.executable, "-c", "pass"), request.root)

    host = {key: value for key, value in os.environ.items() if key.upper() == "SYSTEMROOT"}
    runner = AdapterRunner(Passing(), host, tmp_path / "logs")  # type: ignore[arg-type]

    assert asyncio.run(runner(tmp_path, 20.0)) is TestVerdict.PASSED
    assert [spec.low_priority for spec in specs] == [True]


class _Plan:
    """An adapter whose test command is the one given."""

    def __init__(self, *command: str) -> None:
        self.command = command

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan:
        return CommandPlan(self.command, request.root)


def _host() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key.upper() == "SYSTEMROOT"}


def test_the_runner_makes_its_folder_of_logs_with_the_parents_and_leaves_no_log_in_it(
    tmp_path: Path,
) -> None:
    logs = tmp_path / "not" / "there" / "yet"
    runner = AdapterRunner(_Plan(sys.executable, "-c", "pass"), _host(), logs)  # type: ignore[arg-type]

    assert asyncio.run(runner(tmp_path, 20.0)) is TestVerdict.PASSED
    assert asyncio.run(runner(tmp_path, 20.0)) is TestVerdict.PASSED  # the folder is there by now

    assert list(logs.iterdir()) == []


def test_a_test_command_that_cannot_start_means_the_tests_did_not_run(tmp_path: Path) -> None:
    missing = str(tmp_path / "no-such-program")
    runner = AdapterRunner(_Plan(missing), _host(), tmp_path / "logs")  # type: ignore[arg-type]

    with pytest.raises(TestsDidNotRun, match="spawn_failed"):
        asyncio.run(runner(tmp_path, 20.0))


def judge(scene: Scene) -> object:
    from forja.checks.changes import read_changes  # noqa: PLC0415

    git = scene.repo.git.at(scene.repo.path)
    changes = asyncio.run(read_changes(git))
    tools = scene.measurer._tools(sinks, Level.ATTEMPT, "a")  # noqa: SLF001
    check = MutationCheck(
        tools,
        scene.adapter,
        scene.repo.path,
        changes,
        ("src/sample/mod.py",),
        scene.repo.env,
    )
    return asyncio.run(check.evaluate(gate_input()))


@pytest.mark.slow
def test_a_new_line_the_tests_check_is_approved(repo: Repo, tmp_path: Path) -> None:
    scene = Scene(repo, tmp_path, real=True)
    scene.write(
        "src/sample/mod.py",
        "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
        "def sub(a: int, b: int) -> int:\n    return a - b\n",
    )
    scene.write(
        "tests/test_mod.py",
        "from sample.mod import add, sub\n\n\n"
        "def test_add() -> None:\n    assert add(1, 2) == 3\n\n\n"
        "def test_sub() -> None:\n    assert sub(3, 1) == 2\n",
    )

    assert isinstance(judge(scene), Approved)


@pytest.mark.slow
def test_a_new_line_a_test_runs_without_checking_it_is_rejected_with_the_score(
    repo: Repo, tmp_path: Path
) -> None:
    scene = Scene(repo, tmp_path, real=True)
    scene.write(
        "src/sample/mod.py",
        "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
        "def sub(a: int, b: int) -> int:\n    return a - b\n",
    )
    scene.write(
        "tests/test_mod.py",
        "from sample.mod import add, sub\n\n\n"
        "def test_add() -> None:\n    add(1, 2)\n    sub(3, 1)\n",
    )

    result = judge(scene)

    assert isinstance(result, Rejected)
    assert result.findings[0].measured == 0.0
    assert result.findings[0].limit == 0.70
    assert any(f.target == "src/sample/mod.py:6" for f in result.findings)


@pytest.mark.slow
def test_a_new_line_no_test_runs_is_rejected_for_the_test_author_to_cover(
    repo: Repo, tmp_path: Path
) -> None:
    scene = Scene(repo, tmp_path, real=True)
    scene.write(
        "src/sample/mod.py",
        "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
        "def sub(a: int, b: int) -> int:\n    return a - b\n",
    )
    scene.write(
        "tests/test_mod.py",
        "from sample.mod import add\n\n\ndef test_add() -> None:\n    assert add(1, 2) == 3\n",
    )

    result = judge(scene)

    assert isinstance(result, Rejected)
    assert [f.target for f in result.findings] == ["src/sample/mod.py:6"]
    assert {f.kind for f in result.findings} == {FindingKind.UNTESTED_LINE}


def test_untested_lines_are_missed_lines_and_mutable_lines_of_files_the_tool_never_saw() -> None:
    report = CoverageReport(
        {"a.py": FileCoverage(covered=frozenset({1}), missed=frozenset({2, 9}))}
    )
    lines = {"a.py": frozenset({1, 2, 3}), "b.py": frozenset({4})}
    mutants = [Mutant("b.py", 4, "- -> +", "f", b"", covered=False)]

    assert untested_lines(lines, mutants, report) == {"a.py": [2], "b.py": [4]}
    assert untested_lines({"a.py": frozenset({1})}, [], report) == {}


def test_a_language_gets_only_its_own_files_and_script_languages_share_rules() -> None:
    files = ("a.py", "b.go", "C.java", "D.cs", "e.ts", "f.js", "g.tsx", "notes.md")

    assert of_language("python", files) == ("a.py",)
    assert of_language("go", files) == ("b.go",)
    assert of_language("java", files) == ("C.java",)
    assert of_language("csharp", files) == ("D.cs",)
    assert of_language("typescript", files) == ("e.ts", "f.js", "g.tsx")
    assert of_language("javascript", files) == ("e.ts", "f.js", "g.tsx")
    assert of_language("rust", files) == ()


SHAPES = "def f(a, b):\n    x = a + b\n    return a - b\n\n\ndef g(a, b):\n    return a * b\n"


def test_a_function_that_holds_a_changed_line_is_mutated_whole_and_the_others_are_not(
    tmp_path: Path,
) -> None:
    (tmp_path / "m.py").write_text(SHAPES)
    coverage = CoverageReport({"m.py": FileCoverage(covered=frozenset(range(1, 8)), tests={})})

    mutants = build_mutants(tmp_path, {"m.py": frozenset({3})}, coverage)

    assert [(m.line, m.change) for m in mutants] == [(2, "+ -> -"), (3, "- -> +")]


def test_a_changed_line_with_no_site_still_marks_its_function_as_changed(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(SHAPES)
    coverage = CoverageReport({"m.py": FileCoverage(covered=frozenset(range(1, 8)), tests={})})

    mutants = build_mutants(tmp_path, {"m.py": frozenset({1})}, coverage)

    assert [m.line for m in mutants] == [2, 3]


def test_a_method_and_the_function_in_it_are_changed_together(tmp_path: Path) -> None:
    source = (
        "class C:\n    def outer(self, a):\n        def inner(b):\n            return b + 1\n"
        "        return a - 1\n\n    def other(self, a):\n        return a + 1\n"
    )
    (tmp_path / "m.py").write_text(source)
    coverage = CoverageReport({"m.py": FileCoverage(covered=frozenset(range(1, 9)), tests={})})

    mutants = build_mutants(tmp_path, {"m.py": frozenset({4})}, coverage)

    assert sorted({m.line for m in mutants}) == [4, 5]  # not the line 8 of the other method


def test_without_whole_functions_the_neighbours_of_a_changed_line_are_left_alone(
    tmp_path: Path,
) -> None:
    (tmp_path / "m.py").write_text(SHAPES)
    coverage = CoverageReport({"m.py": FileCoverage(covered=frozenset(range(1, 8)), tests={})})

    mutants = build_mutants(tmp_path, {"m.py": frozenset({3})}, coverage, whole_functions=False)

    assert [m.line for m in mutants] == [3]


# -- the tests of its lines judge a mutant first ---------------------------------------------------


def test_a_mutant_carries_the_id_the_mutator_gives_it_and_the_lines_that_count_for_it(
    tmp_path: Path,
) -> None:
    source = "def f(a, b):\n    return (\n        a\n        - b\n    )\n"
    (tmp_path / "m.py").write_text(source)
    coverage = CoverageReport({"m.py": FileCoverage(covered=frozenset({2}), tests={})})

    (mutant,) = build_mutants(tmp_path, {"m.py": frozenset({4})}, coverage)

    (site,) = discover(source, "m.py")
    assert mutant.id == mutant_id("m.py", site)
    assert json.loads(mutant.id) == ["m.py", "f", site.start, site.end, "-", "+"]
    assert " " not in mutant.id  # one form, whatever writes it
    assert mutant.lines == (4, 2) == site.lines  # its own, and the one the statement starts on


def test_the_test_that_killed_last_time_runs_ahead_and_the_others_by_their_ids() -> None:
    tests = frozenset({"t/c", "t/a", "t/b"})

    assert in_order(tests, None) == ("t/a", "t/b", "t/c")
    assert in_order(tests, "t/c") == ("t/c", "t/a", "t/b")
    assert in_order(tests, "t/gone") == ("t/a", "t/b", "t/c")  # no longer a test of the lines
    assert in_order(frozenset(), "t/a") == ()


class _ByTests:
    """An adapter that can run only the tests asked for, and has no smaller command for a file.
    ``limit`` is how many tests fit in one command."""

    def __init__(self, limit: int = 99) -> None:
        self.limit = limit

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan | NotSupported:
        if len(request.tests) > self.limit:
            return NotSupported(operation.value, "more tests than fit in one command")
        return CommandPlan(("tool", "test", *request.tests), request.root)


def _on(file: str, line: int, covered: bool = True) -> Mutant:
    return Mutant(file, line, "change", "f", b"", covered, id=f"{file}:{line}", lines=(line,))


NAMED = CoverageReport(
    {
        "a.py": FileCoverage(
            covered=frozenset({1, 2, 3}),
            tests={1: frozenset({"t::b", "t::a"}), 2: frozenset({"t::c"})},
        )
    }
)


def test_a_mutant_is_judged_first_by_the_tests_of_its_lines_each_with_its_runner(
    tmp_path: Path,
) -> None:
    whole = AdapterRunner(_ByTests(), {}, tmp_path / "logs")  # type: ignore[arg-type]
    mutants = [_on("a.py", 1), _on("a.py", 2)]

    first, second = judged_first(whole, tmp_path, mutants, NAMED, {"a.py:1": "t::b"})

    assert isinstance(first.first, AdapterRunner) and isinstance(second.first, AdapterRunner)
    # the one that killed it the last time ahead
    assert first.first.tests == ("t::b", "t::a")
    assert second.first.tests == ("t::c",)
    assert first.first.command(tmp_path) == ("tool", "test", "t::b", "t::a")
    assert (first.first.mutated, second.first.mutated) == ("a.py", "a.py")
    # a runner for each, and each with its own place for the test that fails
    assert first.first is not second.first
    assert first.first.killer is not second.first.killer is not whole.killer
    assert (first.first.ids, first.first.logs) == (whole.ids, whole.logs)
    assert [(m.file, m.line, m.id) for m in (first, second)] == [
        ("a.py", 1, "a.py:1"),
        ("a.py", 2, "a.py:2"),
    ]


def test_the_tests_of_every_line_that_counts_for_the_mutant_judge_it(tmp_path: Path) -> None:
    whole = AdapterRunner(_ByTests(), {}, tmp_path / "logs")  # type: ignore[arg-type]
    spanning = replace(_on("a.py", 2), lines=(2, 1))

    (mutant,) = judged_first(whole, tmp_path, [spanning], NAMED)

    assert isinstance(mutant.first, AdapterRunner)
    assert mutant.first.tests == ("t::a", "t::b", "t::c")


def test_a_mutant_whose_lines_have_no_test_named_is_judged_as_before(tmp_path: Path) -> None:
    whole = AdapterRunner(_ByTests(), {}, tmp_path / "logs")  # type: ignore[arg-type]
    mutants = [
        _on("a.py", 3),  # run, by no test the report names
        _on("b.py", 1),  # a file the report does not have
        _on("a.py", 1, covered=False),  # not to be run at all
    ]

    assert judged_first(whole, tmp_path, mutants, NAMED) == mutants
    # and with no coverage at all nothing is chosen
    assert judged_first(whole, tmp_path, [_on("a.py", 1)]) == [_on("a.py", 1)]


def test_tests_the_adapter_cannot_run_alone_leave_the_mutant_to_the_smaller_command_of_its_file(
    tmp_path: Path,
) -> None:
    class ByPackageOnly(_ByPackage):
        def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan:
            if request.tests:
                return NotSupported(operation.value, "no such filter")  # type: ignore[return-value]
            return super().plan(operation, request)

    whole = AdapterRunner(ByPackageOnly(), {}, tmp_path / "logs")  # type: ignore[arg-type]
    named = CoverageReport(
        {"a/x.go": FileCoverage(covered=frozenset({1}), tests={1: frozenset({"a/TestX"})})}
    )

    (mutant,) = judged_first(whole, tmp_path, [_on("a/x.go", 1)], named)

    assert isinstance(mutant.first, AdapterRunner)
    assert (mutant.first.tests, mutant.first.mutated) == ((), "a/x.go")
    assert mutant.first.command(tmp_path) == ("tool", "test", "./a")


@pytest.mark.parametrize("bad", ["t::a\nt::z", "t::a\x00", "t::\tb"])
def test_a_test_id_with_a_control_character_is_handed_to_no_tool(tmp_path: Path, bad: str) -> None:
    whole = AdapterRunner(_ByTests(), {}, tmp_path / "logs")  # type: ignore[arg-type]
    named = CoverageReport(
        {
            "a.py": FileCoverage(
                covered=frozenset({1, 2}),
                tests={1: frozenset({"t::ok", bad}), 2: frozenset({"t::ok"})},
            )
        }
    )

    first, second = judged_first(whole, tmp_path, [_on("a.py", 1), _on("a.py", 2)], named)

    assert first.first is None  # judged by the whole command: nothing of the id reaches a tool
    assert isinstance(second.first, AdapterRunner) and second.first.tests == ("t::ok",)


def test_more_tests_than_fit_in_a_command_leave_the_mutant_to_the_whole_command(
    tmp_path: Path,
) -> None:
    whole = AdapterRunner(_ByTests(limit=1), {}, tmp_path / "logs")  # type: ignore[arg-type]

    first, second = judged_first(whole, tmp_path, [_on("a.py", 1), _on("a.py", 2)], NAMED)

    assert first.first is None  # two tests: too many
    assert isinstance(second.first, AdapterRunner) and second.first.tests == ("t::c",)


def test_an_adapter_that_answers_the_whole_command_for_the_tests_gives_no_first_run(
    tmp_path: Path,
) -> None:
    whole = AdapterRunner(_ByPackage(whole=True), {}, tmp_path / "logs")  # type: ignore[arg-type]
    mutants = [_on("a.py", 1)]

    assert judged_first(whole, tmp_path, mutants, NAMED) == mutants


def test_the_runner_of_the_tests_asks_for_them_in_order_of_a_copy_tested_again(
    tmp_path: Path,
) -> None:
    asked: list[OperationRequest] = []

    class Recording:
        def plan(self, operation: Operation, request: OperationRequest) -> NotSupported:
            asked.append(request)
            return NotSupported(operation.value, "only listening")

    runner = AdapterRunner(Recording(), {}, tmp_path, mutated="a.py", tests=("t::b", "t::a"))  # type: ignore[arg-type]

    assert runner.command(tmp_path / "copy") is None
    assert asked == [
        OperationRequest(root=tmp_path / "copy", rerun=True, mutated="a.py", tests=("t::b", "t::a"))
    ]


def test_runners_that_ask_the_same_adapter_for_the_same_are_equal_and_no_other(
    tmp_path: Path,
) -> None:
    adapter = _ByTests()
    whole = AdapterRunner(adapter, {}, tmp_path / "logs")  # type: ignore[arg-type]
    one = replace(whole, mutated="a.py", tests=("t::a", "t::b"), killer=Killer())
    same = replace(whole, mutated="a.py", tests=("t::a", "t::b"), killer=Killer("t::a"))

    # the two share a baseline in the executor, and each keeps the test that failed for it
    assert one == same and hash(one) == hash(same)
    assert one is not same and one.killer is not same.killer
    assert len({one: 1, same: 2}) == 1
    for other in (
        whole,
        replace(one, tests=("t::b", "t::a")),  # another order is another command
        replace(one, tests=("t::a",)),
        replace(one, mutated="b.py"),
        replace(one, adapter=_ByTests()),  # type: ignore[arg-type]
        "not a runner",
    ):
        assert one != other
    assert whole == AdapterRunner(adapter, {}, tmp_path / "elsewhere")  # type: ignore[arg-type]


def test_mutants_of_a_line_get_equal_runners_and_those_of_another_line_do_not(
    tmp_path: Path,
) -> None:
    whole = AdapterRunner(_ByTests(), {}, tmp_path / "logs")  # type: ignore[arg-type]
    mutants = [_on("a.py", 1), replace(_on("a.py", 1), id="a.py:1b"), _on("a.py", 2)]

    first, again, other = judged_first(whole, tmp_path, mutants, NAMED)

    assert first.first == again.first and first.first is not again.first
    assert first.first != other.first


# -- which test failed -----------------------------------------------------------------------------


def _outcome(test: str, value: str, measurement: str = "test.outcome") -> EvidenceDraft:
    return EvidenceDraft(measurement, Target(kind=TargetKind.SYMBOL, id=test), value, "outcome")


class _Reading:
    """An adapter whose reading of any output is the one given."""

    def __init__(self, *drafts: EvidenceDraft, unreadable: bool = False) -> None:
        self.drafts, self.unreadable = drafts, unreadable
        self.read: list[tuple[Operation, ToolOutput]] = []

    def parse(self, operation: Operation, output: ToolOutput) -> Parsed | Unparseable:
        self.read.append((operation, output))
        return Unparseable("no idea") if self.unreadable else Parsed(self.drafts)


def test_the_test_that_failed_is_the_first_asked_for_that_the_output_shows_as_failed(
    tmp_path: Path,
) -> None:
    output = ToolOutput(1, tmp_path / "x.log", tmp_path)
    adapter = _Reading(
        _outcome("t::a", "passed"),
        _outcome("t::b", "failed"),
        _outcome("t::c", "error"),
        _outcome("t::d", "failed"),  # not one of those asked for
        _outcome("t::a", "failed", measurement="test.failure"),  # not an outcome
    )

    assert failed_test(adapter, output, ("t::a", "t::c", "t::b")) == "t::c"  # type: ignore[arg-type]
    assert failed_test(adapter, output, ("t::a",)) is None  # type: ignore[arg-type]
    assert failed_test(adapter, output, ()) is None  # type: ignore[arg-type]
    assert adapter.read[0] == (Operation.TEST, output)


def test_output_that_cannot_be_read_names_no_test(tmp_path: Path) -> None:
    output = ToolOutput(1, tmp_path / "x.log", tmp_path)

    assert failed_test(_Reading(unreadable=True), output, ("t::a",)) is None  # type: ignore[arg-type]
    assert failed_test(_Reading(), output, ("t::a",)) is None  # type: ignore[arg-type]


def test_an_adapter_says_which_id_asks_for_a_test_reported_by_another(tmp_path: Path) -> None:
    class ByMethod(_Reading):
        def selector(self, test_id: str) -> str:
            return test_id.split("[")[0]

    adapter = ByMethod(_outcome("T#sum[2]", "failed"), _outcome("T#other[1]", "passed"))
    output = ToolOutput(1, tmp_path / "x.log", tmp_path)

    assert failed_test(adapter, output, ("T#other", "T#sum")) == "T#sum"  # type: ignore[arg-type]


def test_in_a_project_below_the_root_the_output_is_read_by_the_adapter_of_the_project(
    tmp_path: Path,
) -> None:
    class Python(_Reading):
        language = "python"

    inner = Python(_outcome("tests/test_a.py::test_x", "failed"))
    output = ToolOutput(1, tmp_path / "x.log", tmp_path)

    # read through the project, the id would have gained its prefix and matched nothing
    assert (
        failed_test(ScopedAdapter(inner, "svc"), output, ("tests/test_a.py::test_x",))  # type: ignore[arg-type]
        == "tests/test_a.py::test_x"
    )


class _Failing:
    """An adapter whose test command ends with ``code`` and whose output shows ``failed``."""

    def __init__(self, code: int, *failed: str) -> None:
        self.code, self.failed = code, failed
        self.logs: list[str] = []

    def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan:
        script = f"import sys; print('ran'); sys.exit({self.code})"
        return CommandPlan((sys.executable, "-c", script), request.root)

    def parse(self, operation: Operation, output: ToolOutput) -> Parsed:
        self.logs.append(output.log_path.read_text(encoding="utf-8"))
        assert (output.exit_code, output.root) == (self.code, output.root)
        return Parsed(tuple(_outcome(test, "failed") for test in self.failed))


def test_the_runner_of_the_tests_leaves_the_one_that_failed(tmp_path: Path) -> None:
    adapter = _Failing(1, "t::b")
    runner = AdapterRunner(adapter, _host(), tmp_path / "logs", tests=("t::a", "t::b"))  # type: ignore[arg-type]

    assert asyncio.run(runner(tmp_path, 20.0)) is TestVerdict.FAILED

    assert runner.killer.test == "t::b"
    assert len(adapter.logs) == 1 and "ran" in adapter.logs[0]  # read before the log is removed
    assert list((tmp_path / "logs").iterdir()) == []


def test_a_run_of_the_tests_that_passes_or_shows_no_failed_test_leaves_none(
    tmp_path: Path,
) -> None:
    passing = _Failing(0, "t::a")
    runner = AdapterRunner(passing, _host(), tmp_path / "logs", tests=("t::a",))  # type: ignore[arg-type]
    runner.killer.test = "t::a"  # from the run before, of the mutant

    assert asyncio.run(runner(tmp_path, 20.0)) is TestVerdict.PASSED
    assert runner.killer.test is None
    assert passing.logs == []  # nothing failed: nothing to read

    broken = AdapterRunner(_Failing(2), _host(), tmp_path / "logs", tests=("t::a",))
    assert asyncio.run(broken(tmp_path, 20.0)) is TestVerdict.FAILED  # it did not build, say
    assert broken.killer.test is None


def test_the_runner_of_the_whole_command_reads_no_output(tmp_path: Path) -> None:
    adapter = _Failing(1, "t::a")
    runner = AdapterRunner(adapter, _host(), tmp_path / "logs")  # type: ignore[arg-type]

    assert asyncio.run(runner(tmp_path, 20.0)) is TestVerdict.FAILED
    assert adapter.logs == [] and runner.killer.test is None


def test_a_run_of_the_tests_out_of_time_leaves_no_test(tmp_path: Path) -> None:
    class Endless(_Failing):
        def plan(self, operation: Operation, request: OperationRequest) -> CommandPlan:
            return CommandPlan((sys.executable, "-c", "import time; time.sleep(60)"), request.root)

    runner = AdapterRunner(Endless(1, "t::a"), _host(), tmp_path / "logs", tests=("t::a",))  # type: ignore[arg-type]
    runner.killer.test = "t::a"

    assert asyncio.run(runner(tmp_path, 0.5)) is TestVerdict.TIMED_OUT
    assert runner.killer.test is None


# -- what is kept from one attempt to the next -----------------------------------------------------


def test_the_tests_that_killed_are_kept_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "not" / "there" / "killers.json"

    save_killers(path, {"m2": "t::b", "m1": "t::a"})

    assert load_killers(path) == {"m1": "t::a", "m2": "t::b"}
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document == {"version": 1, "killers": {"m1": "t::a", "m2": "t::b"}}
    assert list(document["killers"]) == ["m1", "m2"]  # sorted: the same history, the same file


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"version": 1}',
        '{"version": 1, "killers": []}',
        '{"version": 2, "killers": {"m": "t"}}',
        '{"killers": {"m": "t"}}',
    ],
)
def test_a_history_that_cannot_be_read_is_no_history(tmp_path: Path, text: str) -> None:
    path = tmp_path / "killers.json"
    path.write_text(text, encoding="utf-8")

    assert load_killers(path) == {}


def test_what_is_not_a_test_id_is_left_out_of_the_history_read(tmp_path: Path) -> None:
    path = tmp_path / "killers.json"
    path.write_text('{"version": 1, "killers": {"m1": "t::a", "m2": 3, "m3": ""}}', "utf-8")

    assert load_killers(path) == {"m1": "t::a"}


def test_without_a_place_for_it_no_history_is_kept(tmp_path: Path) -> None:
    save_killers(None, {"m": "t"})

    assert load_killers(None) == {}
    assert load_killers(tmp_path / "missing.json") == {}
    assert list(tmp_path.iterdir()) == []


def test_a_history_that_cannot_be_written_stops_nothing(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("x", encoding="utf-8")

    save_killers(tmp_path / "file" / "killers.json", {"m": "t"})  # its folder is a file


def test_the_killers_of_a_run_are_those_of_the_mutants_their_tests_killed(tmp_path: Path) -> None:
    whole = AdapterRunner(_ByTests(), {}, tmp_path / "logs")  # type: ignore[arg-type]
    killed, survived = judged_first(whole, tmp_path, [_on("a.py", 1), _on("a.py", 2)], NAMED)
    assert isinstance(killed.first, AdapterRunner)
    killed.first.killer.test = "t::a"
    by_package = replace(_on("b.py", 1), first=replace(whole, mutated="b.py", killer=Killer("x")))

    found = killers_of([killed, survived, by_package, _on("c.py", 1)])

    # not the one whose tests passed, nor the ones no test of their lines judged
    assert found == {"a.py:1": "t::a"}


@pytest.mark.slow
def test_the_gate_judges_by_the_tests_of_the_lines_and_keeps_which_one_killed(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = Scene(repo, tmp_path, real=True)
    scene.write(
        "src/sample/mod.py",
        "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
        "def sub(a: int, b: int) -> int:\n    return a - b\n",
    )
    scene.write(
        "tests/test_mod.py",
        "from sample.mod import add, sub\n\n\n"
        "def test_add() -> None:\n    assert add(1, 2) == 3\n\n\n"
        "def test_sub() -> None:\n    assert sub(3, 1) == 2\n",
    )
    scene.write(
        "tests/test_other.py",
        "from sample.mod import sub\n\n\ndef test_runs_it() -> None:\n    sub(3, 1)\n",
    )
    commands: list[tuple[str, ...]] = []
    run = ProcessRunner.run

    async def noted(self: ProcessRunner, operation_id: str, spec: ProcessSpec, log: Path) -> object:
        if operation_id.startswith("mutant:"):
            commands.append(tuple(spec.command))
        else:
            measured.append(operation_id)
        return await run(self, operation_id, spec, log)

    measured: list[str] = []
    monkeypatch.setattr(ProcessRunner, "run", noted)
    history = tmp_path / "measure" / "killers.json"

    def judged() -> object:
        git = scene.repo.git.at(scene.repo.path)
        from forja.checks.changes import read_changes  # noqa: PLC0415

        changes = asyncio.run(read_changes(git))
        tools = scene.measurer._tools(sinks, Level.ATTEMPT, "a")  # noqa: SLF001
        check = MutationCheck(
            tools,
            scene.adapter,
            scene.repo.path,
            changes,
            ("src/sample/mod.py",),
            scene.repo.env,
            history=history,
        )
        return asyncio.run(check.evaluate(gate_input(limits={"workers": 1})))

    assert isinstance(judged(), Approved)

    # the check was given no place to share the coverage, and still measured it only once
    assert len([run for run in measured if ":coverage:" in run]) == 1
    killed_by = "tests/test_mod.py::test_sub"
    (killer,) = set(load_killers(history).values())
    assert killer == killed_by
    asked = [tuple(part for part in command if "::" in part) for command in commands]
    # the tests of the line, in the order of their ids: once on the unchanged copy, then for each
    # mutant, stopping at the first that fails. All were killed, so the whole command never ran
    assert len(asked) > 1
    assert set(asked) == {(killed_by, "tests/test_other.py::test_runs_it")}
    assert all("-x" in command for command in commands)

    commands.clear()
    scene.write(
        "tests/test_first.py",
        "from sample.mod import sub\n\n\ndef test_runs_it_too() -> None:\n    sub(3, 1)\n",
    )

    assert isinstance(judged(), Approved)
    # the test that killed runs ahead of one whose id would come before it
    ahead = (
        killed_by,
        "tests/test_first.py::test_runs_it_too",
        "tests/test_other.py::test_runs_it",
    )
    assert {tuple(p for p in command if "::" in p) for command in commands} == {ahead}
