"""The planner: the worst functions first, within the budget, with the limits of the budget."""

import asyncio
from collections.abc import Sequence
from pathlib import Path

from forja.core.artifacts.models import Producer
from forja.orchestrator.config import Budget, FixRequest
from forja.orchestrator.planner import ComplexityPlanner, FixPlanner
from forja.orchestrator.ports import Baseline, PlannedTask

FOUND = {
    ("src/a.py", "calm"): 3,
    ("src/a.py", "tangled"): 22,
    ("src/b.py", "worse"): 40,
    ("src/b.py", "limit"): 15,
    ("src/c.py", "tie_b"): 20,
    ("src/c.py", "tie_a"): 20,
}


def plan(budget: Budget = Budget(), max_ccn: int = 15) -> list[PlannedTask]:  # noqa: B008
    async def functions(worktree: Path) -> dict[tuple[str, str], int]:
        return FOUND

    planner = ComplexityPlanner(functions, max_ccn)
    return list(asyncio.run(planner.plan(Path("."), Baseline(True), budget)))


def test_the_most_complex_functions_come_first_and_only_those_above_the_limit() -> None:
    tasks = plan()

    assert [t.intent for t in tasks] == [
        "reduce the complexity of worse in src/b.py",
        "reduce the complexity of tangled in src/a.py",
        "reduce the complexity of tie_a in src/c.py",
        "reduce the complexity of tie_b in src/c.py",
    ]


def test_each_file_has_the_limit_of_its_own() -> None:
    async def functions(worktree: Path) -> dict[tuple[str, str], int]:
        return FOUND

    planner = ComplexityPlanner(functions, lambda file: 10 if file == "src/c.py" else 30)

    tasks = asyncio.run(planner.plan(Path("."), Baseline(True), Budget()))

    assert [t.scope for t in tasks] == [("src/b.py",), ("src/c.py",), ("src/c.py",)]


def test_a_function_at_the_limit_is_not_above_it() -> None:
    assert all("limit" not in t.intent for t in plan())
    assert any("limit" in t.intent for t in plan(max_ccn=14))


def test_each_task_may_touch_its_file_within_the_budgets_limits() -> None:
    task = plan(Budget(files_per_task=2, lines_per_task=50))[0]

    assert task.scope == ("src/b.py",)
    assert (task.max_files, task.max_lines) == (2, 50)


def test_the_plan_has_no_more_tasks_than_the_budget_allows() -> None:
    assert len(plan(Budget(tasks=2))) == 2
    assert len(plan(Budget(tasks=1))) == 1


def test_nothing_to_do_is_an_empty_plan() -> None:
    assert plan(max_ccn=100) == []


def test_equally_complex_functions_are_ordered_by_file_before_name() -> None:
    async def functions(worktree: Path) -> dict[tuple[str, str], int]:
        return {("src/b.py", "a_name"): 20, ("src/a.py", "z_name"): 20}

    tasks = asyncio.run(ComplexityPlanner(functions, 15).plan(Path("."), Baseline(True), Budget()))

    assert [t.scope for t in tasks] == [("src/a.py",), ("src/b.py",)]


def test_a_fix_plan_puts_the_numbered_criteria_in_the_intent_of_its_one_task() -> None:
    fix = FixRequest.model_validate(
        {
            "description": "page 1 is wrong",
            "criteria": {
                "criteria": [
                    {"id": 1, "text": "page 1 is the first items", "level": "unit", "origin": "x"},
                    {"id": 2, "text": "the last page is shorter", "level": "unit", "origin": "x"},
                ]
            },
        }
    )

    plan = asyncio.run(FixPlanner(fix, ("src",)).plan(Path("."), Baseline(True), Budget()))

    assert [task.intent for task in plan] == [
        "page 1 is wrong\n\nCriteria:\n1. page 1 is the first items\n2. the last page is shorter"
    ]


CHURN = {"src/a.py": 9, "src/b.py": 1, "src/c.py": 2}


def hotspots(
    budget: Budget = Budget(),  # noqa: B008
    churn: dict[str, int] | None = None,
    weak: tuple[tuple[str, str], ...] = (),
    asked: list[list[tuple[str, str]]] | None = None,
) -> list[PlannedTask]:
    async def functions(worktree: Path) -> dict[tuple[str, str], int]:
        return FOUND

    async def changes(worktree: Path) -> dict[str, int]:
        return CHURN if churn is None else churn

    async def weak_nets(
        worktree: Path, found: Sequence[tuple[str, str]]
    ) -> tuple[tuple[str, str], ...]:
        if asked is not None:
            asked.append(list(found))
        return weak

    planner = ComplexityPlanner(functions, 15, churn=changes, weak_nets=weak_nets, test_root="spec")
    return list(asyncio.run(planner.plan(Path("."), Baseline(True), budget)))


def test_a_function_comes_first_by_its_complexity_times_the_changes_of_its_file() -> None:
    tasks = hotspots()

    # tangled 22 x 9 = 198; tie_a and tie_b 20 x 2 = 40; worse 40 x 1 = 40, the most complex of 40
    assert [t.intent for t in tasks] == [
        "reduce the complexity of tangled in src/a.py",
        "reduce the complexity of worse in src/b.py",
        "reduce the complexity of tie_a in src/c.py",
        "reduce the complexity of tie_b in src/c.py",
    ]


def test_a_file_the_history_never_changed_comes_last_and_is_still_planned() -> None:
    tasks = hotspots(churn={"src/a.py": 1, "src/c.py": 1})

    assert [t.scope for t in tasks] == [("src/a.py",), ("src/c.py",), ("src/c.py",), ("src/b.py",)]


def test_a_function_the_tests_do_not_protect_gets_its_tests_first() -> None:
    tasks = hotspots(weak=(("src/b.py", "worse"),))

    assert [(t.stages, t.test_root) for t in tasks] == [
        ((Producer.IMPLEMENTER, Producer.DOCUMENTER), ""),
        ((Producer.TEST_AUTHOR, Producer.IMPLEMENTER, Producer.DOCUMENTER), "spec"),
        ((Producer.IMPLEMENTER, Producer.DOCUMENTER), ""),
        ((Producer.IMPLEMENTER, Producer.DOCUMENTER), ""),
    ]


def test_only_the_functions_the_budget_reaches_have_their_tests_measured() -> None:
    asked: list[list[tuple[str, str]]] = []

    hotspots(Budget(tasks=2), asked=asked)

    assert asked == [[("src/a.py", "tangled"), ("src/b.py", "worse")]]


def test_no_tests_are_measured_for_an_empty_plan() -> None:
    asked: list[list[tuple[str, str]]] = []

    assert hotspots(churn={}, asked=asked, budget=Budget(tasks=1))[0].scope == ("src/b.py",)
    assert asked == [[("src/b.py", "worse")]]

    async def nothing(worktree: Path) -> dict[tuple[str, str], int]:
        return {}

    async def weak_nets(worktree: Path, found: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
        raise AssertionError("nothing was planned")

    planner = ComplexityPlanner(nothing, 15, weak_nets=weak_nets)

    assert asyncio.run(planner.plan(Path("."), Baseline(True), Budget())) == []


def test_without_a_measure_of_the_tests_no_task_has_a_test_author() -> None:
    assert {t.stages for t in plan()} == {(Producer.IMPLEMENTER, Producer.DOCUMENTER)}
    assert {t.test_root for t in plan()} == {""}
