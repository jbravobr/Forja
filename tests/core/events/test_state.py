"""Rules every event must satisfy, and the state rebuilt from events."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from forja.core.events import EventLog, InvalidEvent, RunState
from forja.core.process import ProcessIdentity
from forja.core.states import (
    AttemptState,
    ExecutionState,
    InvalidTransition,
    Level,
    StageState,
    TaskState,
)
from tests.core.events.support import (
    RUN,
    build_event,
    create_run,
    create_task,
    open_log,
    to_execution_state,
)

E = ExecutionState


@pytest.fixture
def log(tmp_path: Path) -> Iterator[EventLog]:
    with open_log(tmp_path / "events.jsonl") as opened:
        yield opened


def test_a_run_starts_in_preparing_and_walks_the_happy_path(log: EventLog) -> None:
    create_run(log)
    to_execution_state(
        log, E.CONFIRMED, E.MEASURING_BASELINE, E.PLANNING, E.EXECUTING, E.CLOSING, E.DELIVERED
    )

    assert log.state.state_of(Level.EXECUTION, RUN) is E.DELIVERED


def test_creation_must_use_the_initial_state_of_the_level(log: EventLog) -> None:
    with pytest.raises(InvalidEvent, match="must start in preparing"):
        log.append_transition(Level.EXECUTION, RUN, None, E.EXECUTING)


def test_a_target_cannot_be_created_twice(log: EventLog) -> None:
    create_run(log)
    with pytest.raises(InvalidEvent, match="already exists"):
        create_run(log)


def test_the_execution_is_the_run_itself(log: EventLog) -> None:
    with pytest.raises(InvalidEvent, match="the run itself"):
        log.append_transition(Level.EXECUTION, "other", None, E.PREPARING)


def test_children_need_an_existing_parent_of_the_right_level(log: EventLog) -> None:
    create_run(log)
    with pytest.raises(InvalidEvent, match="needs a parent_id"):
        log.append_transition(Level.TASK, "t1", None, TaskState.PENDING)
    with pytest.raises(InvalidEvent, match="does not exist"):
        log.append_transition(Level.TASK, "t1", None, TaskState.PENDING, parent_id="nope")
    create_task(log)
    with pytest.raises(InvalidEvent, match="parent stage 'zz'"):
        log.append_transition(Level.ATTEMPT, "a1", None, AttemptState.CREATED, parent_id="zz")
    with pytest.raises(InvalidEvent, match="parent stage 't1'"):
        log.append_transition(Level.ATTEMPT, "a1", None, AttemptState.CREATED, parent_id="t1")

    log.append_transition(Level.STAGE, "s1", None, StageState.PENDING, parent_id="t1")
    log.append_transition(Level.ATTEMPT, "a1", None, AttemptState.CREATED, parent_id="s1")
    assert log.state.parent_of(Level.ATTEMPT, "a1") == "s1"
    assert log.state.children_of(Level.STAGE, "t1") == ["s1"]


def test_transition_of_an_unknown_target_is_rejected(log: EventLog) -> None:
    create_run(log)
    with pytest.raises(InvalidEvent, match="unknown task"):
        log.append_transition(Level.TASK, "ghost", TaskState.PENDING, TaskState.IN_PROGRESS)


def test_origin_must_be_the_current_state(log: EventLog) -> None:
    create_run(log)
    with pytest.raises(InvalidEvent, match="is preparing, not confirmed"):
        log.append_transition(Level.EXECUTION, RUN, E.CONFIRMED, E.MEASURING_BASELINE)


def test_forbidden_transition_is_rejected_and_nothing_is_written(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    with open_log(path) as log:
        create_run(log)
        before = path.read_bytes()

        with pytest.raises(InvalidTransition):
            log.append_transition(Level.EXECUTION, RUN, E.PREPARING, E.DELIVERED)

        assert path.read_bytes() == before
        to_execution_state(log, E.CONFIRMED)  # the log is still usable


def test_unknown_state_name_is_rejected(log: EventLog) -> None:
    create_run(log)
    with pytest.raises(InvalidEvent, match="is not a execution state"):
        log.append_transition(Level.EXECUTION, RUN, E.PREPARING, TaskState.PENDING)


def _running(log: EventLog) -> None:
    create_run(log)
    to_execution_state(log, E.CONFIRMED, E.MEASURING_BASELINE, E.PLANNING, E.EXECUTING)


def test_paused_returns_to_the_state_it_was_paused_from(log: EventLog) -> None:
    _running(log)
    to_execution_state(log, E.PAUSED, E.EXECUTING)

    assert log.state.state_of(Level.EXECUTION, RUN) is E.EXECUTING


def test_paused_cannot_return_anywhere_else(log: EventLog) -> None:
    _running(log)
    to_execution_state(log, E.PAUSED)

    with pytest.raises(InvalidTransition):
        log.append_transition(Level.EXECUTION, RUN, E.PAUSED, E.PLANNING)
    with pytest.raises(InvalidTransition):
        log.append_transition(Level.EXECUTION, RUN, E.PAUSED, E.CLOSING)


def test_failed_returns_to_the_last_state_and_a_failure_during_pause_unwinds_in_order(
    log: EventLog,
) -> None:
    _running(log)
    to_execution_state(log, E.PAUSED, E.FAILED)  # failed while paused
    to_execution_state(log, E.PAUSED)  # back to the last state: paused
    to_execution_state(log, E.EXECUTING)  # and paused goes back to where it began

    assert log.state.state_of(Level.EXECUTION, RUN) is E.EXECUTING


def test_failed_from_a_working_state_returns_to_it(log: EventLog) -> None:
    _running(log)
    to_execution_state(log, E.FAILED)
    with pytest.raises(InvalidTransition):
        log.append_transition(Level.EXECUTION, RUN, E.FAILED, E.PLANNING)

    to_execution_state(log, E.EXECUTING)
    assert log.state.state_of(Level.EXECUTION, RUN) is E.EXECUTING


def test_failed_then_discarded_is_final(log: EventLog) -> None:
    _running(log)
    to_execution_state(log, E.FAILED, E.DISCARDED)

    with pytest.raises(InvalidTransition):
        log.append_transition(Level.EXECUTION, RUN, E.DISCARDED, E.PREPARING)


def test_intent_needs_a_known_target_and_a_fresh_operation_id(log: EventLog) -> None:
    with pytest.raises(InvalidEvent, match="unknown execution"):
        log.append_intent(Level.EXECUTION, RUN, "run_tool", "op1")
    create_run(log)
    log.append_intent(Level.EXECUTION, RUN, "run_tool", "op1")

    with pytest.raises(InvalidEvent, match="already used"):
        log.append_intent(Level.EXECUTION, RUN, "run_tool", "op1")


def test_effect_closes_exactly_the_matching_intent(log: EventLog) -> None:
    create_run(log)
    with pytest.raises(InvalidEvent, match="without an open intent"):
        log.append_effect(Level.EXECUTION, RUN, "run_tool", "op1")
    log.append_intent(Level.EXECUTION, RUN, "run_tool", "op1")
    with pytest.raises(InvalidEvent, match="does not match"):
        log.append_effect(Level.EXECUTION, RUN, "integrate_task", "op1")
    assert set(log.state.open_operations) == {"op1"}

    log.append_effect(Level.EXECUTION, RUN, "run_tool", "op1")

    assert log.state.open_operations == {}
    with pytest.raises(InvalidEvent, match="without an open intent"):
        log.append_effect(Level.EXECUTION, RUN, "run_tool", "op1")
    with pytest.raises(InvalidEvent, match="already used"):
        log.append_intent(Level.EXECUTION, RUN, "run_tool", "op1")


def test_intent_without_effect_is_an_open_operation_with_its_details(log: EventLog) -> None:
    create_run(log)
    log.append_intent(Level.EXECUTION, RUN, "call_agent_cli", "op7")

    (opened,) = log.state.open_operations.values()
    assert (opened.operation, opened.operation_id, opened.level, opened.target_id) == (
        "call_agent_cli",
        "op7",
        Level.EXECUTION,
        RUN,
    )
    assert opened.intent_seq == 2


def test_process_started_is_remembered_until_the_effect(log: EventLog) -> None:
    create_run(log)
    log.append_intent(Level.EXECUTION, RUN, "run_tool", "op1")
    log.append_progress(
        Level.EXECUTION,
        RUN,
        "process_started",
        operation="run_tool",
        operation_id="op1",
        payload={"pid": 4242, "creation_token": 99},
    )

    (identity,) = log.state.leftover_processes()
    assert (identity.pid, identity.creation_token) == (4242, 99)

    log.append_effect(Level.EXECUTION, RUN, "run_tool", "op1")
    assert log.state.leftover_processes() == []


def test_process_started_without_an_open_operation_is_rejected(log: EventLog) -> None:
    create_run(log)
    with pytest.raises(InvalidEvent, match="open operation"):
        log.append_progress(
            Level.EXECUTION, RUN, "process_started", operation_id="nope", payload={"pid": 1}
        )


def test_other_progress_never_changes_the_state(log: EventLog) -> None:
    create_run(log)
    log.append_progress(Level.EXECUTION, RUN, "output", payload={"line": "hello"})

    assert log.state.state_of(Level.EXECUTION, RUN) is E.PREPARING
    assert log.state.open_operations == {}


def test_envelope_rules_run_id_sequence_and_chain() -> None:
    state = RunState("run1")

    with pytest.raises(InvalidEvent, match="not 'run1'"):
        state.validate(build_event(state, run_id="other"))
    with pytest.raises(InvalidEvent, match="expected seq 1"):
        state.validate(build_event(state, seq=5))
    with pytest.raises(InvalidEvent, match="prev does not match"):
        state.validate(build_event(state, prev="1" * 64))

    event = build_event(state)
    state.apply(event)
    assert (state.last_seq, state.last_hash) == (1, event.hash)
    with pytest.raises(InvalidEvent, match="expected seq 2"):
        state.validate(event)


def test_an_open_operation_remembers_what_its_intent_said(log: EventLog) -> None:
    create_run(log)

    log.append_intent(Level.EXECUTION, RUN, "integrate_task", "op1", payload={"branch": "b"})

    (open_operation,) = log.state.open_operations.values()
    assert open_operation.payload == {"branch": "b"}
    assert log.state.effect_of("op1") is None


def test_the_effect_that_closed_an_operation_can_be_read_back(log: EventLog) -> None:
    create_run(log)
    log.append_intent(Level.EXECUTION, RUN, "integrate_task", "op1")

    log.append_effect(Level.EXECUTION, RUN, "integrate_task", "op1", payload={"reconciled": "redo"})

    assert log.state.effect_of("op1") == {"reconciled": "redo"}
    assert log.state.open_operations == {}
    assert log.state.effect_of("never-used") is None


def test_what_an_intent_said_can_be_read_back_after_the_operation_is_closed(log: EventLog) -> None:
    create_run(log)
    log.append_intent(Level.EXECUTION, RUN, "integrate_task", "op1", payload={"branch": "b"})
    while_open = log.state.intent_of("op1")

    log.append_effect(Level.EXECUTION, RUN, "integrate_task", "op1")

    assert while_open == {"branch": "b"}
    assert log.state.intent_of("op1") == {"branch": "b"}
    assert log.state.intent_of("never-used") is None


# -- what the state remembers and how it says no -----------------------------------------------


def test_children_are_found_by_their_level_and_by_their_parent(log: EventLog) -> None:
    create_run(log)
    create_task(log, "t1")
    create_task(log, "t2")
    log.append_transition(Level.STAGE, "s1", None, StageState.PENDING, parent_id="t1")
    log.append_transition(Level.STAGE, "s2", None, StageState.PENDING, parent_id="t2")

    assert log.state.children_of(Level.STAGE, "t1") == ["s1"]
    assert log.state.children_of(Level.TASK, RUN) == ["t1", "t2"]
    assert log.state.children_of(Level.STAGE, RUN) == []


def test_asking_for_a_target_that_does_not_exist_says_which(log: EventLog) -> None:
    create_run(log)

    with pytest.raises(KeyError) as info:
        log.state.state_of(Level.TASK, "t9")

    assert info.value.args == ("unknown task 't9'",)


def test_the_payload_a_target_was_created_with_can_be_read_back(log: EventLog) -> None:
    log.append_transition(Level.EXECUTION, RUN, None, E.PREPARING, payload={"goal": "refactor"})

    assert log.state.transition_payload(Level.EXECUTION, RUN, E.PREPARING) == {"goal": "refactor"}


def start(log: EventLog, **payload: object) -> None:
    create_run(log)
    log.append_intent(Level.EXECUTION, RUN, "call_agent_cli", "op1")
    log.append_progress(
        Level.EXECUTION,
        RUN,
        "process_started",
        operation="call_agent_cli",
        operation_id="op1",
        payload=payload,  # type: ignore[arg-type]
    )


def test_a_started_process_is_remembered_with_or_without_a_creation_token(
    log: EventLog,
) -> None:
    start(log, pid=7, creation_token=None)

    assert log.state.leftover_processes() == [ProcessIdentity(7, None)]


def test_a_started_process_with_a_pid_or_token_of_the_wrong_type_is_not_remembered(
    tmp_path: Path,
) -> None:
    for payload in ({"pid": "abc", "creation_token": None}, {"pid": 7, "creation_token": "x"}):
        with open_log(tmp_path / f"{len(payload)}{payload['pid']}.jsonl") as log:
            start(log, **payload)

            assert log.state.leftover_processes() == []


def test_only_a_process_started_event_can_name_a_process(log: EventLog) -> None:
    create_run(log)
    log.append_intent(Level.EXECUTION, RUN, "call_agent_cli", "op1")

    log.append_progress(
        Level.EXECUTION,
        RUN,
        "output",
        operation="call_agent_cli",
        operation_id="op1",
        payload={"pid": 99, "creation_token": 5},
    )

    assert log.state.leftover_processes() == []


def rejected(state: RunState, event: object) -> str:
    with pytest.raises(InvalidEvent) as info:
        state.validate(event)  # type: ignore[arg-type]
    return str(info.value)


def test_an_event_that_does_not_follow_the_last_one_is_refused_in_words(log: EventLog) -> None:
    create_run(log)

    assert rejected(log.state, build_event(log.state, prev="f" * 64)) == (
        "prev does not match the hash of the previous event"
    )


def test_a_transition_from_a_state_the_level_does_not_have_is_refused_in_words(
    log: EventLog,
) -> None:
    create_run(log)
    create_task(log, "t1")
    event = build_event(
        log.state,
        "transition",
        level="task",
        target_id="t1",
        from_state="bogus",
        to_state="in_progress",
        parent_id=None,
        payload={},
    )

    assert rejected(log.state, event) == "'bogus' is not a task state"


def test_the_execution_cannot_have_a_parent(log: EventLog) -> None:
    event = build_event(
        log.state,
        "transition",
        target_id=RUN,
        from_state=None,
        to_state="preparing",
        parent_id="somewhere",
        payload={},
    )

    assert rejected(log.state, event) == "the execution is the run itself and has no parent"


def test_a_process_started_event_needs_an_open_operation(log: EventLog) -> None:
    create_run(log)
    event = build_event(
        log.state, payload={"kind": "process_started", "pid": 1}, operation="x", operation_id="x1"
    )

    assert rejected(log.state, event) == "process_started needs an open operation"


def test_a_paused_target_remembers_where_it_came_from(log: EventLog) -> None:
    create_run(log)
    to_execution_state(log, E.CONFIRMED, E.MEASURING_BASELINE, E.PAUSED)

    assert log.state.previous_state(Level.EXECUTION, RUN) is E.MEASURING_BASELINE


def test_a_target_that_was_never_paused_has_no_previous_state(log: EventLog) -> None:
    create_run(log)
    to_execution_state(log, E.CONFIRMED)

    assert log.state.previous_state(Level.EXECUTION, RUN) is None


def test_going_back_from_a_pause_clears_the_previous_state(log: EventLog) -> None:
    create_run(log)
    to_execution_state(log, E.CONFIRMED, E.MEASURING_BASELINE, E.PAUSED)
    log.append_transition(Level.EXECUTION, RUN, E.PAUSED, E.MEASURING_BASELINE)

    assert log.state.previous_state(Level.EXECUTION, RUN) is None


def test_the_previous_state_is_the_latest_one_left_when_a_pause_turns_into_a_failure(
    log: EventLog,
) -> None:
    create_run(log)
    to_execution_state(log, E.CONFIRMED, E.MEASURING_BASELINE, E.PAUSED, E.FAILED)

    assert log.state.previous_state(Level.EXECUTION, RUN) is E.PAUSED
