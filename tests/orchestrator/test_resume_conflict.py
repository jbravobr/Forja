"""A run whose second task does not merge resumes, from any cut, to the same end.

``test_resume_rollback`` cuts around the integration the gate rejects. Here the merge itself does
not apply: two tasks leave the same commit and write the same file differently. The workspace
undoes the merge on its own, so no ``rollback_integration`` is written; the cuts fall before,
inside and after the ``integrate_task`` of the task that clashes.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from forja.core.events import Event, EventLog, LogUnusable
from forja.core.states import ExecutionState, Level, TaskState
from forja.orchestrator.config import RunConfig
from forja.orchestrator.outcome import Outcome
from forja.orchestrator.ports import AgentResult, AttemptContext
from tests.orchestrator.crash import Counting, CrashAfter, Interrupted
from tests.orchestrator.fakes import ScriptedAgent, ScriptedPlanner
from tests.orchestrator.harness import RUN, World
from tests.orchestrator.test_together import APART

# Lines the uninterrupted scenario writes. The two tasks start together, so the order of the
# lines before the first merge is not fixed; from the intent of the merge that clashes on, only
# one task is left and every line has its place. The cuts begin on the last line before that
# intent: after every event that is not a progress line, and a torn line before every intent and
# effect. A test fails if they drift.
TOTAL_LINES = 75
FIRST_CUT = 66
CUTS = (66, 67, 68, 69, 70, 72, 73, 74)
TORN_CUTS = (66, 68, 71, 72)

pytestmark = pytest.mark.slow


@dataclass
class OneAfterTheOther(ScriptedAgent):
    """Both tasks leave the same commit, and the second only works once the first joined the run.

    The first call waits for the second to be in its own, with its branch already made; the
    second waits for the merge of the first. What each one writes clashes with the other.
    """

    edits: Any = ({"src/shared.py": "one\n"}, {"src/shared.py": "two\n"})
    second_began: asyncio.Event = field(default_factory=asyncio.Event)
    first_joined: asyncio.Event = field(default_factory=asyncio.Event)

    def watch(self, event: Event) -> None:
        if (
            event.type == "transition"
            and event.level is Level.TASK
            and event.target_id == "t1"
            and event.to_state == "integrated"
        ):
            self.first_joined.set()

    async def call(self, ctx: AttemptContext, operation_id: str, sink: object) -> AgentResult:
        if ctx.task.task_id == "t1":
            await asyncio.wait_for(self.second_began.wait(), timeout=60)
        else:
            self.second_began.set()
            await asyncio.wait_for(self.first_joined.wait(), timeout=60)
        return await super().call(ctx, operation_id, sink)  # type: ignore[arg-type]


@dataclass
class Idle(ScriptedAgent):
    """The agent of a resume that has nothing left to ask of it."""

    async def call(self, ctx: AttemptContext, operation_id: str, sink: object) -> AgentResult:
        raise AssertionError(f"the resume called the agent again, for {ctx.task.task_id}")


def clashing(world: World, log: EventLog, agent: ScriptedAgent) -> tuple[Outcome, EventLog]:
    config: RunConfig = world.config.model_copy(update={"parallel_tasks": 2})
    return world.run(agent=agent, planner=ScriptedPlanner(APART), config=config, log=log)


def first_run(world: World, log: EventLog) -> tuple[Outcome, EventLog]:
    agent = OneAfterTheOther()
    log.subscribe(agent.watch)
    return clashing(world, log, agent)


def facts(world: World, log: EventLog) -> dict[str, Any]:
    """What two runs must share to have ended in the same place; commits are compared by tree."""
    state = log.state
    run_branch = world.workspace.run_branch
    return {
        "execution": state.state_of(Level.EXECUTION, RUN).value,
        "tasks": [state.state_of(Level.TASK, task).value for task in ("t1", "t2")],
        "blocked": state.transition_payload(Level.TASK, "t2", TaskState.BLOCKED),
        "open_operations": sorted(state.open_operations),
        "refs": sorted(world.repo.sh("for-each-ref", "--format=%(refname)").splitlines()),
        "run_tree": world.repo.sh("rev-parse", f"{run_branch}^{{tree}}"),
        "commits": world.repo.sh("rev-list", "--count", run_branch),
        "worktrees": len(world.repo.sh("worktree", "list").splitlines()),
        "status": world.repo.sh("status", "--porcelain"),
        "summary": (world.run_dir / "resumo.md")
        .read_text(encoding="utf-8")
        .replace(str(world.run_dir), "<run>"),
        "blobs": sorted(p.name for p in (world.run_dir / "store" / "blobs").glob("*")),
    }


@pytest.fixture
def world(tmp_path: Path, template: Path) -> World:
    return World.create(tmp_path / "world", template)


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory, template: Path) -> dict[str, Any]:
    """An uninterrupted run: its facts, and the line each event landed on."""
    world = World.create(tmp_path_factory.mktemp("conflict") / "world", template)
    counter = Counting()
    marks: list[tuple[int, str, str, str]] = []
    log = world.open(write=counter)
    log.subscribe(
        lambda e: marks.append(
            (counter.count, e.type, getattr(e, "operation", ""), getattr(e, "target_id", ""))
        )
    )
    outcome, log = first_run(world, log)
    return {
        "outcome": outcome,
        "facts": facts(world, log),
        "lines": counter.count,
        "marks": marks,
    }


def test_the_uninterrupted_run_blocks_the_task_that_does_not_merge_and_keeps_the_other(
    reference: dict[str, Any],
) -> None:
    found = reference["facts"]

    assert reference["outcome"].state is ExecutionState.DELIVERED
    assert found["tasks"] == ["integrated", "blocked"]
    assert found["blocked"]["reason"] == "integration_conflict"
    assert found["open_operations"] == [] and found["status"] == ""
    operations = [operation for _, kind, operation, _ in reference["marks"] if kind == "effect"]
    assert operations.count("integrate_task") == 2
    assert "rollback_integration" not in operations


def test_the_uninterrupted_run_has_the_shape_the_cuts_were_made_for(
    reference: dict[str, Any],
) -> None:
    marks = reference["marks"]

    assert reference["lines"] == TOTAL_LINES
    merge = next(
        n
        for n, kind, name, task in marks
        if (kind, name, task) == ("intent", "integrate_task", "t2")
    )
    assert merge - 1 == FIRST_CUT
    boundaries = {n for n, kind, _, _ in marks if kind != "progress"}
    assert tuple(sorted(n for n in boundaries if n >= FIRST_CUT)) == CUTS
    torn = {n - 1 for n, kind, _, _ in marks if kind in ("intent", "effect")}
    assert tuple(sorted(n for n in torn if n >= FIRST_CUT)) == TORN_CUTS


CASES = [(cut, False) for cut in CUTS] + [(cut, True) for cut in TORN_CUTS]


@pytest.mark.parametrize(
    ("cut", "torn"), CASES, ids=[f"{cut}-{'torn' if torn else 'clean'}" for cut, torn in CASES]
)
def test_interrupted_around_the_merge_that_clashes_the_run_resumes_to_the_same_end(
    world: World, reference: dict[str, Any], cut: int, torn: bool
) -> None:
    try:
        first_run(world, world.open(write=CrashAfter(cut, torn=torn)))
    except (Interrupted, LogUnusable):
        pass  # the runner may report the dead log instead of the failed write itself
    else:
        raise AssertionError("the run ended before the cut")

    outcome, log = clashing(world, world.open(), Idle())

    assert outcome.state is reference["outcome"].state
    assert facts(world, log) == reference["facts"]
