"""What an execution has used so far, read from its events.

There is no separate file for this: a resumed process reads the log again and gets the same
answer. Time is the sum of the sessions (``session_started`` up to ``session_ended``, or up to the
last event when the process died), so the hours a run spent paused do not count.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from forja.core.events.models import Event
from forja.core.events.state import RunState
from forja.core.events.versioning import BadLine, parse_line
from forja.core.states import AttemptState, Level

SESSION_STARTED = "session_started"
SESSION_ENDED = "session_ended"
AGENT_ACTIVITY = "agent_activity"
USAGE = "usage"


@dataclass(frozen=True)
class Consumption:
    seconds: float
    tokens: int
    unmeasured: int = 0  # calls to the agent whose CLI did not say what they used


def _moment(event: Event) -> datetime:
    return datetime.fromisoformat(event.time)


def read_consumption(log_path: Path) -> Consumption:
    """Time and tokens spent according to the log; a torn last line is ignored."""
    if not log_path.exists():
        return Consumption(0.0, 0)
    tally = _Tally()
    for raw in [line for line in log_path.read_bytes().split(b"\n") if line]:
        try:
            event = parse_line(raw)
        except BadLine:
            break
        tally.add(event)
    tally.close_open_session()
    return Consumption(tally.seconds, tally.tokens, tally.unmeasured)


class _Tally:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.tokens = 0
        self.unmeasured = 0
        self._started: datetime | None = None
        self._last: datetime | None = None  # read only after the first event sets it

    def add(self, event: Event) -> None:
        moment = _moment(event)
        if event.type == "progress":
            self._progress(event, moment)
        self._last = moment

    def close_open_session(self) -> None:
        if self._started is not None and self._last is not None:
            self.seconds += (self._last - self._started).total_seconds()

    def _progress(self, event: Event, moment: datetime) -> None:
        kind = event.payload.get("kind")
        if kind == SESSION_STARTED:
            self.close_open_session()  # the earlier session never ended
            self._started = moment
        elif kind == SESSION_ENDED and self._started is not None:
            self.seconds += (moment - self._started).total_seconds()
            self._started = None
        elif kind == AGENT_ACTIVITY and event.payload.get("type") == USAGE:
            sent, received = event.payload.get("input"), event.payload.get("output")
            self.tokens += _count(sent) + _count(received)
            self.unmeasured += sent is None or received is None


def _count(value: object) -> int:
    return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else 0


class Meter:
    """What the run has spent, read from its events, against the tokens it was given.

    Time is only shown, never a limit. ``enforce`` says whether passing the limit stops the run:
    with a person watching it does not (they are shown how far over they are and decide), without
    one there is nobody to decide.
    """

    def __init__(
        self,
        limit_tokens: int,
        log_path: Path,
        *,
        enforce: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit_tokens
        self._path = log_path
        self._enforce = enforce
        self._before = read_consumption(log_path).seconds
        self._clock = clock
        self._started = clock()

    def used(self) -> int:
        return read_consumption(self._path).tokens

    def fraction_used(self) -> float:
        return self.used() / self._limit

    def exceeded(self) -> bool:
        return self.used() >= self._limit

    def exhausted(self) -> bool:
        return self._enforce and self.exceeded()

    def elapsed(self) -> float:
        return self._before + (self._clock() - self._started)


_COUNTED = (AttemptState.REJECTED, AttemptState.APPROVED, AttemptState.COMMITTED)


def quality_attempts(state: RunState, stage_id: str) -> int:
    """Attempts that judged the agent's work; discarded ones measure instability, not quality."""
    return sum(
        state.state_of(Level.ATTEMPT, attempt) in _COUNTED
        for attempt in state.children_of(Level.ATTEMPT, stage_id)
    )


def consecutive_infra_failures(state: RunState, stage_id: str) -> int:
    """Infrastructure failures at the end of the stage's attempts, with nothing else after them."""
    count = 0
    for attempt in reversed(state.children_of(Level.ATTEMPT, stage_id)):
        if state.state_of(Level.ATTEMPT, attempt) is not AttemptState.DISCARDED:
            break
        payload = state.transition_payload(Level.ATTEMPT, attempt, AttemptState.DISCARDED) or {}
        if payload.get("infra_failure") is not True:
            break
        count += 1
    return count
