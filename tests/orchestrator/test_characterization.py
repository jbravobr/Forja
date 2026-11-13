"""A refactoring of code the tests do not protect: the test author goes first, its tests pass."""

from pathlib import Path

import pytest

from forja.core.artifacts.models import Producer
from forja.core.states import ExecutionState, Level, StageState, TaskState
from forja.orchestrator.config import ExecutorRef, FixRequest, Objective
from forja.orchestrator.ports import PlannedTask
from forja.orchestrator.task_cycle import CHARACTERIZE
from tests.orchestrator.fakes import (
    PASSED,
    UNTESTED,
    ScriptedAgent,
    ScriptedMeasurer,
    ScriptedPlanner,
)
from tests.orchestrator.harness import MemoryWorld

INTENT = "reduce the complexity of f in src/a.py"
TASK = PlannedTask(
    INTENT,
    ("src/a.py",),
    8,
    400,
    stages=(Producer.TEST_AUTHOR, Producer.IMPLEMENTER),
    test_root="tests",
)
TESTS = {"tests/test_a.py": "def test_a():\n    assert True\n"}
MORE = {"tests/test_more.py": "def test_more():\n    assert True\n"}
CODE = {"src/a.py": "def f():\n    return 1\n"}


@pytest.fixture
def world(tmp_path: Path) -> MemoryWorld:
    found = MemoryWorld(tmp_path)
    found.config = found.config.model_copy(
        update={"test_author": ExecutorRef(name="author-cli", path="/bin/author", version="2.0")}
    )
    return found


def test_the_tests_are_committed_before_the_refactoring_and_frozen_for_it(
    world: MemoryWorld,
) -> None:
    author, implementer = ScriptedAgent(edits=[TESTS]), ScriptedAgent(edits=[CODE])

    outcome, log = world.run(
        measurer=ScriptedMeasurer(),
        agent=implementer,
        agents={Producer.TEST_AUTHOR: author},
        planner=ScriptedPlanner((TASK,)),
    )

    assert outcome.state is ExecutionState.DELIVERED
    assert log.state.state_of(Level.TASK, "t1") is TaskState.INTEGRATED
    assert log.state.state_of(Level.STAGE, "t1-t") is StageState.COMMITTED
    memory = world.memory
    reachable = [
        memory.commits[sha] for sha in memory.ancestors(memory.tip(world.workspace.run_branch).sha)
    ]
    tests = next(c for c in reachable if c.subject.startswith("test: "))
    work = next(c for c in reachable if c.subject.startswith("refactor: "))
    assert memory.is_ancestor(tests.sha, work.sha)
    assert tests.subject == f"test: {CHARACTERIZE} {INTENT}"
    assert work.subject == f"refactor: {INTENT}"
    assert tests.trailer("Agent") == ["author-cli 2.0"]
    assert implementer.seen[0].task.frozen_digest


def test_every_stage_of_the_task_knows_its_tests_describe_the_code_as_it_is(
    world: MemoryWorld,
) -> None:
    author, implementer = ScriptedAgent(edits=[TESTS]), ScriptedAgent(edits=[CODE])

    world.run(
        measurer=ScriptedMeasurer(),
        agent=implementer,
        agents={Producer.TEST_AUTHOR: author},
        planner=ScriptedPlanner((TASK,)),
    )

    first, second = author.seen[0].task, implementer.seen[0].task
    assert (first.role, first.characterizes) == (Producer.TEST_AUTHOR, True)
    assert (second.role, second.characterizes) == (Producer.IMPLEMENTER, True)
    assert first.intent == f"{CHARACTERIZE} {INTENT}"
    assert second.intent == INTENT


def test_tests_of_code_that_exists_answer_to_no_criterion(world: MemoryWorld) -> None:
    measurer = ScriptedMeasurer()

    world.run(
        measurer=measurer,
        agent=ScriptedAgent(edits=[CODE]),
        agents={Producer.TEST_AUTHOR: ScriptedAgent(edits=[TESTS])},
        planner=ScriptedPlanner((TASK,)),
    )

    assert measurer.expected == [None, None]


def test_a_refactoring_with_no_test_author_characterizes_nothing(world: MemoryWorld) -> None:
    implementer = ScriptedAgent(edits=[CODE])

    world.run(measurer=ScriptedMeasurer(), agent=implementer, planner=ScriptedPlanner())

    assert implementer.seen[0].task.characterizes is False


def test_the_tests_of_a_fix_are_not_tests_of_the_code_as_it_is(world: MemoryWorld) -> None:
    fix = FixRequest.model_validate(
        {
            "description": "page 1 is wrong",
            "criteria": {"criteria": [{"id": 1, "text": "page 1", "level": "unit", "origin": "x"}]},
        }
    )
    world.config = world.config.model_copy(update={"objective": Objective.FIX, "fix": fix})
    author, implementer = ScriptedAgent(edits=[TESTS]), ScriptedAgent(edits=[CODE])

    world.run(
        measurer=ScriptedMeasurer(),
        agent=implementer,
        agents={Producer.TEST_AUTHOR: author},
        planner=ScriptedPlanner((TASK,)),
    )

    assert author.seen[0].task.characterizes is False
    assert author.seen[0].task.intent == INTENT
    assert implementer.seen[0].task.characterizes is False


def test_the_tests_asked_for_new_lines_are_told_the_work_they_test(world: MemoryWorld) -> None:
    author = ScriptedAgent(edits=[TESTS, MORE])
    measurer = ScriptedMeasurer(verdicts=(PASSED, UNTESTED, PASSED))

    outcome, _ = world.run(
        measurer=measurer,
        agent=ScriptedAgent(edits=[CODE]),
        agents={Producer.TEST_AUTHOR: author},
        planner=ScriptedPlanner((TASK,)),
    )

    assert outcome.state is ExecutionState.DELIVERED
    extra = author.seen[1].task
    assert extra.reviews == "t1-s"
    assert extra.intent == INTENT
