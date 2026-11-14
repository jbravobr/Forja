"""The mutation check: pure pieces first, then the real thing over a small project."""

import asyncio
from pathlib import Path

import pytest

from forja.checks.base import Undecided
from forja.checks.changes import Changes
from forja.checks.mutation import (
    DEFAULT_MIN_SCORE,
    MutationCheck,
    build_mutants,
    changed_lines,
    min_score,
    of_language,
    untested_lines,
    worker_count,
)
from forja.core.gates import GateInput
from forja.core.gates.models import (
    Approved,
    FindingKind,
    Inconclusive,
    InconclusiveReason,
    Rejected,
)
from forja.core.reports.models import CoverageReport, FileCoverage
from forja.core.states import Level
from forja.mutation.executor import Mutant
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
