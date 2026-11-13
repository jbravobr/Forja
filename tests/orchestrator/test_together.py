"""Tasks of disjoint scope that run at once: the schedule, the limit, one integration at a time."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from forja.core.events import EventLog
from forja.core.idempotency.reconcilers import task_branch_name
from forja.core.states import AttemptState, ExecutionState, Level, TaskState
from forja.orchestrator.config import RunConfig
from forja.orchestrator.phases import Orchestrator
from forja.orchestrator.ports import AgentResult, AttemptContext, PlannedTask
from forja.orchestrator.task_cycle import TaskCycle
from tests.orchestrator.fakes import ScriptedAgent, ScriptedPlanner
from tests.orchestrator.harness import RUN, MemoryWorld

APART = (
    PlannedTask("work on a", ("src/a.py",), 8, 400),
    PlannedTask("work on b", ("src/b.py",), 8, 400),
)
TOUCHING = (
    PlannedTask("work on the package", ("src",), 8, 400),
    PlannedTask("work on a", ("src/a.py",), 8, 400),
)


@dataclass
class Watched(ScriptedAgent):
    """Counts the calls under way at once; with ``meet`` set, a call waits for that many."""

    meet: int = 0
    fail_on: str = ""
    inside: int = 0
    most: int = 0
    _met: asyncio.Event | None = field(default=None, repr=False)

    async def call(self, ctx: AttemptContext, operation_id: str, sink: object) -> AgentResult:
        self.inside += 1
        self.most = max(self.most, self.inside)
        try:
            if self.meet:
                self._met = self._met or asyncio.Event()
                if self.inside >= self.meet:
                    self._met.set()
                await asyncio.wait_for(self._met.wait(), timeout=10)
            else:
                await asyncio.sleep(0.05)
            if ctx.task.task_id == self.fail_on:
                raise RuntimeError("the service fell over")
            return await super().call(ctx, operation_id, sink)  # type: ignore[arg-type]
        finally:
            self.inside -= 1


@pytest.fixture
def world(tmp_path: Path) -> MemoryWorld:
    return MemoryWorld(tmp_path)


def together(
    world: MemoryWorld, tasks: tuple[PlannedTask, ...], agent: Watched, n: int
) -> EventLog:
    config: RunConfig = world.config.model_copy(update={"parallel_tasks": n})
    outcome, log = world.run(agent=agent, planner=ScriptedPlanner(tasks), config=config)
    assert outcome.state is ExecutionState.DELIVERED
    return log


def states(log: EventLog) -> list[object]:
    return [log.state.state_of(Level.TASK, t) for t in ("t1", "t2")]


def test_two_tasks_of_disjoint_scope_run_at_once_and_both_join_the_run(
    world: MemoryWorld,
) -> None:
    agent = Watched(meet=2)  # each call waits for the other: they would deadlock one at a time

    log = together(world, APART, agent, 2)

    assert states(log) == [TaskState.INTEGRATED] * 2
    assert agent.most == 2


def test_a_merge_that_does_not_apply_blocks_its_task_and_leaves_the_other_running(
    world: MemoryWorld,
) -> None:
    agent = Watched(edits=({"src/shared.py": "one\n"}, {"src/shared.py": "two\n"}), meet=2)

    log = together(world, APART, agent, 2)

    assert sorted(str(s) for s in states(log)) == sorted(
        str(s) for s in (TaskState.INTEGRATED, TaskState.BLOCKED)
    )
    blocked = next(t for t in ("t1", "t2") if states(log)[int(t[1]) - 1] is TaskState.BLOCKED)
    payload = log.state.transition_payload(Level.TASK, blocked, TaskState.BLOCKED)
    assert payload is not None and payload["reason"] == "integration_conflict"
    assert log.state.open_operations == {}


def test_tasks_whose_scopes_touch_never_run_together(world: MemoryWorld) -> None:
    agent = Watched()

    log = together(world, TOUCHING, agent, 2)

    assert states(log) == [TaskState.INTEGRATED] * 2
    assert agent.most == 1


def test_the_limit_of_one_keeps_disjoint_tasks_one_after_the_other(world: MemoryWorld) -> None:
    agent = Watched()

    log = together(world, APART, agent, 1)

    assert states(log) == [TaskState.INTEGRATED] * 2
    assert agent.most == 1


def test_the_limit_caps_how_many_run_at_once(world: MemoryWorld) -> None:
    tasks = (*APART, PlannedTask("work on c", ("src/c.py",), 8, 400))
    agent = Watched()

    together(world, tasks, agent, 2)

    assert agent.most == 2


def test_both_merges_are_in_the_run_branch(world: MemoryWorld) -> None:
    together(world, APART, Watched(meet=2), 2)

    subjects = [c.subject for c in world.memory.log(world.workspace.run_branch)]
    assert {"merge: work on a", "merge: work on b"} <= set(subjects)


def test_a_task_that_falls_over_takes_the_running_ones_down_without_a_hang(
    world: MemoryWorld,
) -> None:
    agent = Watched(meet=2, fail_on="t1")
    config: RunConfig = world.config.model_copy(update={"parallel_tasks": 2})

    with pytest.raises(RuntimeError, match="fell over"):
        world.run(agent=agent, planner=ScriptedPlanner(APART), config=config)

    assert agent.inside == 0


# -- what the guard on git still holds the agent to ----------------------------------------------


def theirs(tmp_path: Path) -> object:
    from forja.orchestrator.workspace_git import Layout, others_work  # noqa: PLC0415

    return others_work("r1", Layout(tmp_path / "wt"), "t1", "t1-a1", lambda: ["t1", "t2"])


def difference(kind: str, subject: str) -> object:
    from forja.core.gitguard.snapshot import Difference, DifferenceKind  # noqa: PLC0415

    return Difference(DifferenceKind(kind), subject)


def test_the_branches_and_worktrees_forja_keeps_for_other_tasks_are_not_the_agents(
    tmp_path: Path,
) -> None:
    ignore = theirs(tmp_path)
    assert ignore(difference("ref_added", "refs/heads/forja/r1/t2"))  # type: ignore[operator]
    assert ignore(difference("ref_moved", "refs/heads/forja-run/r1"))  # type: ignore[operator]
    # the note of a commit another task made while this agent ran
    assert ignore(difference("ref_added", "refs/notes/commits"))  # type: ignore[operator]
    assert ignore(difference("ref_moved", "refs/notes/commits"))  # type: ignore[operator]
    assert ignore(difference("worktree_added", str(tmp_path / "wt" / "t2-a1")))  # type: ignore[operator]
    assert ignore(difference("index_changed", str(tmp_path / "wt" / "run")))  # type: ignore[operator]


def test_the_agents_own_branch_and_worktree_and_everything_outside_the_run_stay_watched(
    tmp_path: Path,
) -> None:
    ignore = theirs(tmp_path)
    for kind, subject in (
        ("ref_moved", "refs/heads/forja/r1/t1"),
        ("ref_added", "refs/heads/sneaky"),
        ("ref_added", "refs/heads/forja/other-run/t2"),
        ("ref_added", "refs/heads/forja/r1/t9"),  # not a task of the run
        ("index_changed", str(tmp_path / "wt" / "t1-a1")),
        ("index_changed", str(tmp_path / "repo")),
        ("config_changed", "config"),
        ("hooks_changed", "hooks"),
    ):
        assert not ignore(difference(kind, subject)), (kind, subject)  # type: ignore[operator]


def test_one_task_at_a_time_holds_the_agent_to_the_branches_of_the_other_tasks_too(
    world: MemoryWorld,
) -> None:
    other = task_branch_name(RUN, "t2")
    agent = Watched(meddle=lambda: world.memory.branches.setdefault(other, world.memory.base))
    config: RunConfig = world.config.model_copy(update={"parallel_tasks": 1})

    _, log = world.run(agent=agent, planner=ScriptedPlanner(APART), config=config)

    payload = log.state.transition_payload(Level.ATTEMPT, "t1-a1", AttemptState.REJECTED)
    assert payload is not None
    assert other in str(payload["reason"])


def test_the_limit_of_one_never_enters_the_scheduler_of_tasks_together(
    world: MemoryWorld, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered: list[int] = []
    original = Orchestrator._execute_together

    async def spy(self: Orchestrator, cycle: TaskCycle, limit: int) -> None:
        entered.append(limit)
        await original(self, cycle, limit)

    monkeypatch.setattr(Orchestrator, "_execute_together", spy)
    together(world, APART, Watched(), 1)
    assert entered == []
    together(MemoryWorld(tmp_path / "two"), APART, Watched(), 2)
    assert entered == [2]
