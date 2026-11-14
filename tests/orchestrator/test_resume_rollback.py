"""A run whose integration gate rejects the task resumes, from any cut, to the same end.

The scenario of ``test_resume_end_to_end`` never undoes an integration. Here the gate of the
integration rejects the task, so the cuts fall before, inside and after ``rollback_integration``.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from forja.core.events import EventLog, LogUnusable, SinkFactory
from forja.core.gates import PipelineOutcome
from forja.core.states import ExecutionState, Level, TaskState
from forja.orchestrator.outcome import Outcome
from tests.orchestrator.crash import Counting, CrashAfter, Interrupted
from tests.orchestrator.fakes import REJECTED, ContentMeasurer, ScriptedAgent
from tests.orchestrator.harness import World
from tests.orchestrator.resume_support import (
    agent_was_running,
    fingerprint,
    scenario,
    with_one_more_discarded_attempt,
    without_discarded_in_summary,
)

# Lines the uninterrupted scenario writes. Up to the integration it is the scenario of
# ``test_resume_end_to_end``, which is cut there; the cuts here begin on the last line before
# the intent of ``integrate_task``: after every event that is not a progress line, and a torn
# line before every intent and effect. A test fails if they drift.
TOTAL_LINES = 68
FIRST_CUT = 57
CUTS = (57, 58, 59, 60, 61, 62, 63, 65, 66, 67)
TORN_CUTS = (57, 58, 59, 60, 64, 65)

pytestmark = pytest.mark.slow


@dataclass
class RejectingIntegration(ContentMeasurer):
    """Judges the attempts by what the agent wrote and never lets the task join the run."""

    async def integration(self, worktree: Path, sinks: SinkFactory) -> PipelineOutcome:
        return REJECTED


def rejected_at_integration(world: World, **options: Any) -> tuple[Outcome, EventLog]:
    options.setdefault("agent", ScriptedAgent(in_process=False, by_feedback=True))
    return scenario(world, measurer=RejectingIntegration(), **options)


@pytest.fixture
def world(tmp_path: Path, template: Path) -> World:
    return World.create(tmp_path / "world", template)


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory, template: Path) -> dict[str, Any]:
    """An uninterrupted run: its facts, and the line each event landed on."""
    world = World.create(tmp_path_factory.mktemp("rollback") / "world", template)
    counter = Counting()
    marks: list[tuple[int, str, str]] = []
    log = world.open(write=counter)
    log.subscribe(lambda e: marks.append((counter.count, e.type, getattr(e, "operation", ""))))
    outcome, log = rejected_at_integration(world, log=log)
    return {
        "outcome": outcome,
        "task": log.state.state_of(Level.TASK, "t1"),
        "reason": log.state.transition_payload(Level.TASK, "t1", TaskState.BLOCKED),
        "fingerprint": fingerprint(world, log),
        "lines": counter.count,
        "marks": marks,
    }


def test_the_uninterrupted_run_undoes_the_integration_and_blocks_the_task(
    reference: dict[str, Any],
) -> None:
    fp = reference["fingerprint"]

    assert reference["task"] is TaskState.BLOCKED
    assert reference["reason"]["reason"] == "integration_gate"
    assert fp["attempts"] == {"rejected": 1, "committed": 1}
    assert fp["open_operations"] == [] and fp["infra_failures"] == 0
    operations = [operation for _, kind, operation in reference["marks"] if kind == "effect"]
    assert operations.count("integrate_task") == 1
    assert operations.count("rollback_integration") == 1
    assert operations.index("integrate_task") < operations.index("rollback_integration")


def test_the_uninterrupted_run_has_the_shape_the_cuts_were_made_for(
    reference: dict[str, Any],
) -> None:
    marks = reference["marks"]

    assert reference["lines"] == TOTAL_LINES
    integration = next(n for n, kind, name in marks if (kind, name) == ("intent", "integrate_task"))
    assert integration - 1 == FIRST_CUT
    boundaries = {n for n, kind, _ in marks if kind != "progress"}
    assert tuple(sorted(n for n in boundaries if n >= FIRST_CUT)) == CUTS
    torn = {n - 1 for n, kind, _ in marks if kind in ("intent", "effect")}
    assert tuple(sorted(n for n in torn if n >= FIRST_CUT)) == TORN_CUTS


CASES = [(cut, False) for cut in CUTS] + [(cut, True) for cut in TORN_CUTS]


@pytest.mark.parametrize(
    ("cut", "torn"), CASES, ids=[f"{cut}-{'torn' if torn else 'clean'}" for cut, torn in CASES]
)
def test_interrupted_around_the_rollback_the_run_resumes_to_the_same_end(
    world: World, reference: dict[str, Any], cut: int, torn: bool
) -> None:
    try:
        rejected_at_integration(world, log=world.open(write=CrashAfter(cut, torn=torn)))
    except (Interrupted, LogUnusable):
        pass  # the runner may report the dead log instead of the failed write itself
    else:
        raise AssertionError("the run ended before the cut")
    running = agent_was_running(world)

    outcome, log = rejected_at_integration(world, log=world.open())

    assert outcome.state is reference["outcome"].state
    assert isinstance(outcome.state, ExecutionState)
    expected = reference["fingerprint"]
    reached = fingerprint(world, log)
    if running:
        expected = with_one_more_discarded_attempt(expected)
        reached = without_discarded_in_summary(reached)
    assert reached == expected
