"""The plan of a refactoring run, made without a model: the hotspots first.

A model that picks its own work picks what is interesting, not what is worst. The measure decides,
the model only does the task it is given. A hotspot is complexity times how often the file changes
(Adam Tornhill): complex code nobody touches pays back little, complex code changed every week is
where the work goes.
"""

from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from pathlib import Path

from forja.checks.complexity import DEFAULT_MAX_CCN
from forja.core.artifacts.models import Producer
from forja.orchestrator.config import Budget, CreateRequest, FixRequest
from forja.orchestrator.criteria import with_criteria
from forja.orchestrator.ports import TEST_ROOT, Baseline, PlannedTask

# (file, function) -> cyclomatic complexity
Functions = Callable[[Path], Awaitable[Mapping[tuple[str, str], int]]]
# file -> how many commits of the history changed it
Churn = Callable[[Path], Awaitable[Mapping[str, int]]]
# of the (file, function) pairs asked about, the ones the tests do not protect
WeakNets = Callable[[Path, Sequence[tuple[str, str]]], Awaitable[Collection[tuple[str, str]]]]


class ComplexityPlanner:
    def __init__(
        self,
        functions: Functions,
        max_ccn: int | Callable[[str], int] = DEFAULT_MAX_CCN,
        *,
        churn: Churn | None = None,
        weak_nets: WeakNets | None = None,
        test_root: str = TEST_ROOT,
    ) -> None:
        """``max_ccn`` is one limit, or the limit of each file (the policy sets it per language).

        Without ``churn`` every file counts the same and the order is that of the complexity.
        Without ``weak_nets`` no task gets tests written before it."""
        self._functions = functions
        self._max_ccn = max_ccn if callable(max_ccn) else lambda _file: max_ccn
        self._churn = churn
        self._weak_nets = weak_nets
        self._test_root = test_root

    async def plan(
        self, worktree: Path, baseline: Baseline, budget: Budget
    ) -> Sequence[PlannedTask]:
        found = await self._functions(worktree)
        changes = await self._churn(worktree) if self._churn is not None else None
        worst = sorted(
            (
                (ccn * (1 if changes is None else changes.get(file, 0)), ccn, file, name)
                for (file, name), ccn in found.items()
                if ccn > self._max_ccn(file)
            ),
            key=lambda item: (-item[0], -item[1], item[2], item[3]),
        )
        chosen = [(file, name) for _, _, file, name in worst[: budget.tasks]]
        weak: Collection[tuple[str, str]] = ()
        if self._weak_nets is not None and chosen:
            weak = await self._weak_nets(worktree, chosen)
        return [
            PlannedTask(
                intent=f"reduce the complexity of {name} in {file}",
                scope=(file,),
                max_files=budget.files_per_task,
                max_lines=budget.lines_per_task,
                # code the tests do not protect gets tests of what it does today first
                stages=(
                    *((Producer.TEST_AUTHOR,) if (file, name) in weak else ()),
                    Producer.IMPLEMENTER,
                    Producer.DOCUMENTER,
                ),
                test_root=self._test_root if (file, name) in weak else "",
            )
            for file, name in chosen
        ]


class FixPlanner:
    """The plan of a fix run: one task, the tests first and then the code that makes them pass."""

    def __init__(self, fix: FixRequest, scope: Sequence[str], test_root: str = TEST_ROOT) -> None:
        self._fix = fix
        self._scope = tuple(scope)
        self._test_root = test_root

    def _intent(self) -> str:
        """The defect and, when the person gave them, its criteria; otherwise the specifier writes
        the criteria and the stages after it add them to the intent."""
        criteria = self._fix.criteria
        if criteria is None:
            return self._fix.description
        return with_criteria(self._fix.description, criteria)

    async def plan(
        self, worktree: Path, baseline: Baseline, budget: Budget
    ) -> Sequence[PlannedTask]:
        return [
            PlannedTask(
                intent=self._intent(),
                scope=self._scope,
                max_files=budget.files_per_task,
                max_lines=budget.lines_per_task,
                stages=(
                    *(() if self._fix.criteria else (Producer.SPECIFIER,)),
                    Producer.TEST_AUTHOR,
                    Producer.IMPLEMENTER,
                    Producer.DOCUMENTER,
                ),
                test_root=self._test_root,
            )
        ]


class CreationPlanner(FixPlanner):
    """The plan of a new project: one task from the idea, like a fix with no criteria.

    The scaffold already built, tested and committed the project, so this is a feature on top of
    it: the specifier writes the criteria, the test author writes tests that fail without the
    feature, the implementer makes them pass and the documenter writes it up."""

    def __init__(
        self, request: CreateRequest, scope: Sequence[str], test_root: str = TEST_ROOT
    ) -> None:
        super().__init__(FixRequest(description=request.description), scope, test_root)


class InvestigationPlanner:
    """The plan of an investigation: one task, the archaeologist first and the documenter after.

    The code is not planned from the measure: the question names what to look at, and ``scope``
    narrows where."""

    def __init__(self, question: str, scope: Sequence[str]) -> None:
        self._question = question
        self._scope = tuple(scope)

    async def plan(
        self, worktree: Path, baseline: Baseline, budget: Budget
    ) -> Sequence[PlannedTask]:
        return [
            PlannedTask(
                intent=self._question,
                scope=self._scope,
                max_files=budget.files_per_task,
                max_lines=budget.lines_per_task,
                stages=(Producer.ARCHAEOLOGIST, Producer.DOCUMENTER),
            )
        ]
