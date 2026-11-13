from pathlib import Path
from typing import Any

import pytest

from forja.core.events import EventLog
from forja.core.states import AttemptState, Level, StageState
from forja.orchestrator.budget import (
    Consumption,
    Meter,
    consecutive_infra_failures,
    quality_attempts,
    read_consumption,
)
from tests.core.events.support import RUN, FakeClock, create_run
from tests.orchestrator.support import ManualClock, add_attempt, open_with, start_stage


def note(log: EventLog, kind: str, **payload: Any) -> None:
    log.append_progress(Level.EXECUTION, RUN, kind, payload=payload)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


def test_a_log_that_does_not_exist_has_used_nothing(tmp_path: Path) -> None:
    assert read_consumption(tmp_path / "none.jsonl") == Consumption(0.0, 0)


def test_a_session_counts_from_its_start_to_its_end(tmp_path: Path, clock: ManualClock) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "session_started")
        clock.advance(10)
        note(log, "session_ended")

    assert read_consumption(path).seconds == 10.0


def test_the_time_between_sessions_does_not_count(tmp_path: Path, clock: ManualClock) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "session_started")
        clock.advance(10)
        note(log, "session_ended")
        clock.advance(3600)  # paused overnight
        note(log, "session_started")
        clock.advance(5)
        note(log, "session_ended")

    assert read_consumption(path).seconds == 15.0


def test_a_session_that_died_counts_up_to_its_last_event(
    tmp_path: Path, clock: ManualClock
) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "session_started")
        clock.advance(7)
        note(log, "tick")
        clock.advance(1000)  # nothing was written in all this time: the process was gone
        note(log, "session_started")
        clock.advance(2)
        note(log, "session_ended")

    assert read_consumption(path).seconds == 9.0


def test_a_session_still_open_at_the_end_of_the_log_counts_to_the_last_event(
    tmp_path: Path, clock: ManualClock
) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "session_started")
        clock.advance(4)
        note(log, "tick")

    assert read_consumption(path).seconds == 4.0


def test_an_end_without_a_start_counts_for_nothing(tmp_path: Path, clock: ManualClock) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        clock.advance(50)
        note(log, "session_ended")

    assert read_consumption(path).seconds == 0.0


def test_tokens_are_summed_from_usage_events_and_bad_numbers_are_ignored(
    tmp_path: Path, clock: ManualClock
) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "agent_activity", type="usage", input=100, output=20)
        note(log, "agent_activity", type="usage", input=5)
        note(log, "agent_activity", type="usage", input=True, output=-3)
        note(log, "agent_activity", type="usage", input=1, output=0)
        note(log, "agent_activity", type="usage", input="9", output=1.5)
        note(log, "agent_activity", type="file_edit", input=1000, output=1000)
        note(log, "other", type="usage", input=1000)

    assert read_consumption(path).tokens == 126


def test_a_torn_last_line_is_ignored(tmp_path: Path, clock: ManualClock) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "session_started")
        clock.advance(6)
        note(log, "session_ended")
    with path.open("ab") as handle:
        handle.write(b'{"schema_version":1,"seq":')

    assert read_consumption(path).seconds == 6.0


def usage(log: EventLog, tokens: int) -> None:
    log.append_progress(
        Level.ATTEMPT,
        "t1-a1",
        "agent_activity",
        payload={"type": "usage", "input": tokens, "output": 0},
    )


def test_the_meter_counts_tokens_from_the_log_as_they_arrive(tmp_path: Path) -> None:
    log = EventLog.open(tmp_path / "events.jsonl", "r1", clock=FakeClock())
    meter = Meter(1000, log.path)

    assert (meter.used(), meter.fraction_used(), meter.exceeded()) == (0, 0.0, False)
    usage(log, 250)
    assert (meter.used(), meter.fraction_used(), meter.exceeded()) == (250, 0.25, False)
    usage(log, 750)
    assert (meter.used(), meter.fraction_used(), meter.exceeded()) == (1000, 1.0, True)
    usage(log, 500)
    assert meter.fraction_used() == 1.5


def test_passing_the_limit_stops_the_run_only_when_nobody_is_there_to_decide(
    tmp_path: Path,
) -> None:
    log = EventLog.open(tmp_path / "events.jsonl", "r1", clock=FakeClock())
    usage(log, 100)

    assert Meter(100, log.path, enforce=True).exhausted()
    assert not Meter(100, log.path).exhausted()
    assert not Meter(101, log.path, enforce=True).exhausted()


def test_the_meter_shows_the_time_of_earlier_sessions_plus_this_one(tmp_path: Path) -> None:
    now = [100.0]
    log = EventLog.open(tmp_path / "events.jsonl", "r1", clock=FakeClock())
    meter = Meter(1000, log.path, clock=lambda: now[0])

    assert meter.elapsed() == read_consumption(log.path).seconds
    now[0] += 39.5
    assert meter.elapsed() == read_consumption(log.path).seconds + 39.5


def test_only_attempts_that_judged_the_agents_work_count_as_quality_attempts(
    tmp_path: Path, clock: ManualClock
) -> None:
    with open_with(clock, tmp_path / "events.jsonl") as log:
        start_stage(log)
        add_attempt(log, "a1", AttemptState.REJECTED)
        add_attempt(log, "a2", AttemptState.DISCARDED, infra_failure=True)
        add_attempt(log, "a3", AttemptState.COMMITTED)

        assert quality_attempts(log.state, "s1") == 2
        assert quality_attempts(log.state, "other-stage") == 0


def test_an_attempt_still_running_is_not_counted_yet(tmp_path: Path, clock: ManualClock) -> None:
    with open_with(clock, tmp_path / "events.jsonl") as log:
        start_stage(log)
        log.append_transition(Level.ATTEMPT, "a1", None, AttemptState.CREATED, parent_id="s1")

        assert quality_attempts(log.state, "s1") == 0


def test_infrastructure_failures_are_counted_only_at_the_end_of_the_stage(
    tmp_path: Path, clock: ManualClock
) -> None:
    with open_with(clock, tmp_path / "events.jsonl") as log:
        start_stage(log)
        add_attempt(log, "a1", AttemptState.DISCARDED, infra_failure=True)
        add_attempt(log, "a2", AttemptState.REJECTED)
        add_attempt(log, "a3", AttemptState.DISCARDED, infra_failure=True)
        add_attempt(log, "a4", AttemptState.DISCARDED, infra_failure=True)

        assert consecutive_infra_failures(log.state, "s1") == 2


def test_a_discard_that_was_not_an_infrastructure_failure_ends_the_run_of_failures(
    tmp_path: Path, clock: ManualClock
) -> None:
    with open_with(clock, tmp_path / "events.jsonl") as log:
        start_stage(log)
        add_attempt(log, "a1", AttemptState.DISCARDED, infra_failure=True)
        add_attempt(log, "a2", AttemptState.DISCARDED, infra_failure=False)
        add_attempt(log, "a3", AttemptState.DISCARDED)

        assert consecutive_infra_failures(log.state, "s1") == 0
        assert consecutive_infra_failures(log.state, "none") == 0


def test_infrastructure_failures_from_before_the_last_pause_of_the_stage_are_not_counted(
    tmp_path: Path, clock: ManualClock
) -> None:
    with open_with(clock, tmp_path / "events.jsonl") as log:
        start_stage(log)
        log.append_transition(Level.STAGE, "s1", StageState.PENDING, StageState.IN_PROGRESS)
        add_attempt(log, "a1", AttemptState.DISCARDED, infra_failure=True)
        add_attempt(log, "a2", AttemptState.DISCARDED, infra_failure=True)
        assert consecutive_infra_failures(log.state, "s1") == 2

        log.append_transition(Level.STAGE, "s1", StageState.IN_PROGRESS, StageState.INFRA_PAUSED)
        assert consecutive_infra_failures(log.state, "s1") == 0

        log.append_transition(Level.STAGE, "s1", StageState.INFRA_PAUSED, StageState.IN_PROGRESS)
        assert consecutive_infra_failures(log.state, "s1") == 0
        add_attempt(log, "a3", AttemptState.DISCARDED, infra_failure=True)
        assert consecutive_infra_failures(log.state, "s1") == 1

        log.append_transition(Level.STAGE, "s1", StageState.IN_PROGRESS, StageState.INFRA_PAUSED)
        log.append_transition(Level.STAGE, "s1", StageState.INFRA_PAUSED, StageState.IN_PROGRESS)
        add_attempt(log, "a4", AttemptState.DISCARDED, infra_failure=True)
        add_attempt(log, "a5", AttemptState.DISCARDED, infra_failure=True)
        assert consecutive_infra_failures(log.state, "s1") == 2


def test_the_state_knows_when_each_state_was_last_entered(
    tmp_path: Path, clock: ManualClock
) -> None:
    with open_with(clock, tmp_path / "events.jsonl") as log:
        start_stage(log)
        created = log.state.last_seq
        assert log.state.transition_seq(Level.STAGE, "s1", StageState.PENDING) == created
        assert log.state.transition_seq(Level.STAGE, "s1", StageState.IN_PROGRESS) is None

        note(log, "tick")
        log.append_transition(Level.STAGE, "s1", StageState.PENDING, StageState.IN_PROGRESS)
        assert log.state.transition_seq(Level.STAGE, "s1", StageState.IN_PROGRESS) == created + 2
        assert log.state.transition_seq(Level.STAGE, "s1", StageState.PENDING) == created

        log.append_transition(Level.STAGE, "s1", StageState.IN_PROGRESS, StageState.INFRA_PAUSED)
        log.append_transition(Level.STAGE, "s1", StageState.INFRA_PAUSED, StageState.IN_PROGRESS)
        assert log.state.transition_seq(Level.STAGE, "s1", StageState.IN_PROGRESS) == created + 4


# -- more edges of the reading -------------------------------------------------------------------


def test_sessions_that_died_do_not_wipe_out_the_ones_before_them(
    tmp_path: Path, clock: ManualClock
) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "session_started")
        clock.advance(10)
        note(log, "session_ended")
        note(log, "session_started")
        clock.advance(7)
        note(log, "tick")
        clock.advance(500)
        note(log, "session_started")
        clock.advance(2)
        note(log, "session_ended")

    assert read_consumption(path).seconds == 19.0


def test_an_open_session_at_the_end_adds_to_the_closed_ones(
    tmp_path: Path, clock: ManualClock
) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "session_started")
        clock.advance(10)
        note(log, "session_ended")
        note(log, "session_started")
        clock.advance(4)
        note(log, "tick")

    assert read_consumption(path).seconds == 14.0


def test_only_the_end_marker_ends_a_session(tmp_path: Path, clock: ManualClock) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "session_started")
        clock.advance(3)
        note(log, "tick")
        clock.advance(4)
        note(log, "session_ended")

    assert read_consumption(path).seconds == 7.0


def test_what_is_written_after_a_session_ended_is_not_part_of_it(
    tmp_path: Path, clock: ManualClock
) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "session_started")
        clock.advance(5)
        note(log, "session_ended")
        clock.advance(100)
        note(log, "housekeeping")  # for instance, what a resume writes before the next session
        clock.advance(1)
        note(log, "session_started")
        clock.advance(2)
        note(log, "session_ended")

    assert read_consumption(path).seconds == 7.0


def test_only_progress_events_can_start_or_end_a_session(
    tmp_path: Path, clock: ManualClock
) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "session_started")
        clock.advance(5)
        log.append_intent(Level.EXECUTION, RUN, "op", "op1", payload={"kind": "session_ended"})
        clock.advance(5)
        note(log, "tick")

    assert read_consumption(path).seconds == 10.0


def test_text_with_spaces_in_an_event_does_not_cut_the_reading_short(
    tmp_path: Path, clock: ManualClock
) -> None:
    path = tmp_path / "events.jsonl"
    with open_with(clock, path) as log:
        create_run(log)
        note(log, "output", line="two words here")
        note(log, "session_started")
        clock.advance(8)
        note(log, "session_ended")
        note(log, "agent_activity", type="usage", input=1, output=0)

    result = read_consumption(path)
    assert result.seconds == 8.0 and result.tokens == 1
