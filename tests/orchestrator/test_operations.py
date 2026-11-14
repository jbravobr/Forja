import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from forja.core.events import Event, EventLog, Ref, RefKind
from forja.core.events.versioning import parse_line
from forja.core.idempotency import Decision, OperationName
from forja.core.states import ExecutionState, Level, TaskState
from forja.orchestrator.operations import Journal, Step, StillOpen
from tests.core.events.support import RUN, create_run, open_log


@pytest.fixture
def log(tmp_path: Path) -> Iterator[EventLog]:
    with open_log(tmp_path / "events.jsonl") as opened:
        create_run(opened)
        yield opened


def events(log: EventLog) -> list[Event]:
    return [parse_line(line) for line in log.path.read_bytes().splitlines() if line]


def kinds(log: EventLog) -> list[tuple[str, str | None]]:
    return [(e.type, getattr(e, "operation_id", None)) for e in events(log)]


def step(done: list[str], label: str = "x") -> Step:
    async def run() -> None:
        done.append(label)

    return Step.plain({"label": label}, run)


def operate(journal: Journal, to_run: Step, subject: str = "t1") -> bool:
    return asyncio.run(
        journal.operate(OperationName.CREATE_TASK_BRANCH, Level.EXECUTION, RUN, subject, to_run)
    )


def settle(log: EventLog, op_id: str, decision: Decision) -> None:
    log.append_intent(Level.EXECUTION, RUN, OperationName.CREATE_TASK_BRANCH, op_id)
    log.append_effect(
        Level.EXECUTION,
        RUN,
        OperationName.CREATE_TASK_BRANCH,
        op_id,
        payload={"reconciled": decision.value},
    )


# -- targets -----------------------------------------------------------------------------------


def test_entering_a_state_moves_the_target_once(log: EventLog) -> None:
    journal = Journal(log)

    assert journal.enter(Level.EXECUTION, RUN, ExecutionState.CONFIRMED) is True
    assert journal.enter(Level.EXECUTION, RUN, ExecutionState.CONFIRMED) is False
    assert log.state.state_of(Level.EXECUTION, RUN) is ExecutionState.CONFIRMED


def test_a_new_target_is_created_with_its_parent_and_payload(log: EventLog) -> None:
    journal = Journal(log)

    assert journal.enter(Level.TASK, "t1", TaskState.PENDING, parent=RUN, payload={"n": 1})
    assert log.state.parent_of(Level.TASK, "t1") == RUN
    assert log.state.transition_payload(Level.TASK, "t1", TaskState.PENDING) == {"n": 1}
    assert not journal.enter(Level.TASK, "t1", TaskState.PENDING, parent=RUN)


def test_entering_a_state_that_was_already_left_does_nothing(log: EventLog) -> None:
    journal = Journal(log)
    journal.enter(Level.TASK, "t1", TaskState.PENDING, parent=RUN)
    journal.enter(Level.TASK, "t1", TaskState.IN_PROGRESS)

    assert journal.enter(Level.TASK, "t1", TaskState.PENDING) is False
    assert log.state.state_of(Level.TASK, "t1") is TaskState.IN_PROGRESS


def test_references_and_payloads_travel_with_the_transition(log: EventLog) -> None:
    journal = Journal(log)
    ref = Ref(kind=RefKind.ARTIFACT, id="art1", hash="a" * 64)
    other = Ref(kind=RefKind.ARTIFACT, id="art2", hash="b" * 64)

    journal.enter(Level.TASK, "t1", TaskState.PENDING, parent=RUN, refs=(ref,))
    journal.enter(Level.TASK, "t1", TaskState.IN_PROGRESS, payload={"why": "go"}, refs=(other,))

    transitions = [e for e in events(log) if e.type == "transition" and e.target_id == "t1"]
    assert [e.refs for e in transitions] == [(ref,), (other,)]
    assert log.state.transition_payload(Level.TASK, "t1", TaskState.IN_PROGRESS) == {"why": "go"}


# -- operations --------------------------------------------------------------------------------


def test_an_operation_records_intent_then_work_then_effect(log: EventLog) -> None:
    journal = Journal(log)
    done: list[str] = []

    assert operate(journal, step(done)) is True

    assert done == ["x"]
    assert kinds(log)[-2:] == [
        ("intent", "create_task_branch:t1"),
        ("effect", "create_task_branch:t1"),
    ]
    intent = next(e for e in events(log) if e.type == "intent")
    assert intent.payload == {"label": "x"}


def test_an_operation_that_happened_is_not_done_again_and_its_payload_is_not_read(
    log: EventLog,
) -> None:
    journal = Journal(log)
    done: list[str] = []
    operate(journal, step(done))
    reads: list[int] = []

    async def payload() -> dict[str, Any]:
        reads.append(1)
        return {}

    async def run() -> None:
        done.append("again")

    assert operate(journal, Step(payload, run)) is False
    assert done == ["x"] and reads == []


def test_different_subjects_are_different_operations(log: EventLog) -> None:
    journal = Journal(log)
    done: list[str] = []

    operate(journal, step(done, "a"), "t1")
    operate(journal, step(done, "b"), "t2")

    assert done == ["a", "b"]


def test_an_open_operation_must_be_settled_before_anything_else(log: EventLog) -> None:
    journal = Journal(log)
    log.append_intent(
        Level.EXECUTION, RUN, OperationName.CREATE_TASK_BRANCH, "create_task_branch:t1"
    )

    with pytest.raises(StillOpen, match="create_task_branch:t1 is still open"):
        journal.operation_id(OperationName.CREATE_TASK_BRANCH, "t1")


def test_an_operation_the_resume_decided_to_redo_runs_again_under_a_new_id(
    log: EventLog,
) -> None:
    journal = Journal(log)
    settle(log, "create_task_branch:t1", Decision.REDO)
    done: list[str] = []

    assert journal.done(OperationName.CREATE_TASK_BRANCH, "t1") is False
    assert operate(journal, step(done)) is True

    assert done == ["x"]
    assert log.state.effect_of("create_task_branch:t1:1") == {}
    assert journal.done(OperationName.CREATE_TASK_BRANCH, "t1") is True


def test_an_operation_the_resume_found_already_done_is_not_repeated(log: EventLog) -> None:
    journal = Journal(log)
    settle(log, "create_task_branch:t1", Decision.ALREADY_DONE)
    done: list[str] = []

    assert operate(journal, step(done)) is False
    assert done == []


def test_what_the_intent_said_is_read_from_the_operation_that_happened(log: EventLog) -> None:
    journal = Journal(log)
    name = OperationName.CREATE_TASK_BRANCH
    before = journal.intent_of(name, "t1")
    log.append_intent(Level.EXECUTION, RUN, name, "create_task_branch:t1", payload={"n": 1})
    log.append_effect(
        Level.EXECUTION, RUN, name, "create_task_branch:t1", payload={"reconciled": "redo"}
    )
    redone = journal.intent_of(name, "t1")
    log.append_intent(Level.EXECUTION, RUN, name, "create_task_branch:t1:1", payload={"n": 2})
    still_open = journal.intent_of(name, "t1")
    log.append_effect(Level.EXECUTION, RUN, name, "create_task_branch:t1:1")

    assert (before, redone, still_open) == ({}, {}, {})
    assert journal.intent_of(name, "t1") == {"n": 2}


def test_two_redos_in_a_row_count_up(log: EventLog) -> None:
    journal = Journal(log)
    settle(log, "create_task_branch:t1", Decision.REDO)
    settle(log, "create_task_branch:t1:1", Decision.REDO)

    assert journal.operation_id(OperationName.CREATE_TASK_BRANCH, "t1") == "create_task_branch:t1:2"


def test_the_hook_sees_the_two_points_where_a_process_can_die(log: EventLog) -> None:
    points: list[str] = []
    journal = Journal(log, hook=points.append)
    seen_in_log: list[int] = []

    async def run() -> None:
        seen_in_log.append(len(kinds(log)))

    operate(journal, Step.plain({}, run))

    assert points == ["create_task_branch:after_intent", "create_task_branch:before_effect"]
    assert seen_in_log == [len(kinds(log)) - 1]  # the work ran with the intent written only


def test_a_failing_step_leaves_the_operation_open(log: EventLog) -> None:
    journal = Journal(log)

    async def run() -> None:
        raise RuntimeError("disk gone")

    with pytest.raises(RuntimeError):
        operate(journal, Step.plain({}, run))

    assert "create_task_branch:t1" in log.state.open_operations


# -- notes -------------------------------------------------------------------------------------


def test_sessions_and_notes_are_progress_events_about_the_run(log: EventLog) -> None:
    journal = Journal(log)

    journal.session_started()
    journal.note("phase_skipped", {"phase": "documentation"})
    journal.session_ended("paused")

    last = events(log)[-3:]
    assert [e.payload["kind"] for e in last] == [
        "session_started",
        "phase_skipped",
        "session_ended",
    ]
    assert isinstance(last[0].payload["pid"], int) and last[0].payload["pid"] > 0
    assert last[1].payload["phase"] == "documentation"
    assert last[2].payload["reason"] == "paused"
    assert all(e.level is Level.EXECUTION and e.target_id == RUN for e in last)
    assert journal.run_id == RUN
