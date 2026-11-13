"""Run state rebuilt only from events, and the rules an event must satisfy to be accepted."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import JsonValue

from forja.core.events.errors import InvalidEvent
from forja.core.events.models import (
    GENESIS_HASH,
    EffectEvent,
    Event,
    IntentEvent,
    ProgressEvent,
    TransitionEvent,
)
from forja.core.process import ProcessIdentity
from forja.core.states import (
    ATTEMPT_MACHINE,
    EXECUTION_MACHINE,
    STAGE_MACHINE,
    TASK_MACHINE,
    Level,
    StateMachine,
)

MACHINES: Mapping[Level, StateMachine[Any]] = {
    Level.EXECUTION: EXECUTION_MACHINE,
    Level.TASK: TASK_MACHINE,
    Level.STAGE: STAGE_MACHINE,
    Level.ATTEMPT: ATTEMPT_MACHINE,
}
_PARENT_LEVEL = {Level.TASK: Level.EXECUTION, Level.STAGE: Level.TASK, Level.ATTEMPT: Level.STAGE}
PROCESS_STARTED = "process_started"


@dataclass
class _Target:
    state: StrEnum
    parent_id: str | None
    # States left by entering a resumable state; the top is where "back to previous" goes.
    stack: list[StrEnum] = field(default_factory=list)
    # Payload of the latest transition into each state, by state value (small facts only).
    entered: dict[str, dict[str, JsonValue]] = field(default_factory=dict)
    # Sequence number of the latest transition into each state, by state value.
    entered_seq: dict[str, int] = field(default_factory=dict)


@dataclass
class OpenOperation:
    """An intent with no effect yet: the run was interrupted in the middle of it."""

    operation: str
    operation_id: str
    level: Level
    target_id: str
    intent_seq: int
    process: ProcessIdentity | None = None
    # What the intent said it was going to do; reconciling reads its inputs from here.
    payload: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class _Move:
    target: _Target | None  # None when the event creates the target
    new_state: StrEnum
    new_stack: list[StrEnum]


class RunState:
    """Everything resuming needs, derived by applying events in order.

    ``validate`` checks an event without changing anything; ``apply`` validates and then
    commits. Replaying a log into a fresh ``RunState`` yields the state the run had.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.last_seq = 0
        self.last_hash = GENESIS_HASH
        self._targets: dict[tuple[Level, str], _Target] = {}
        self._open: dict[str, OpenOperation] = {}
        self._closed: dict[str, dict[str, JsonValue]] = {}

    def has_target(self, level: Level, target_id: str) -> bool:
        return (level, target_id) in self._targets

    def state_of(self, level: Level, target_id: str) -> StrEnum:
        try:
            return self._targets[(level, target_id)].state
        except KeyError:
            raise KeyError(f"unknown {level.value} {target_id!r}") from None

    def transition_payload(
        self, level: Level, target_id: str, state: StrEnum
    ) -> dict[str, JsonValue] | None:
        """Payload of the latest transition into ``state``, or ``None`` if it never happened."""
        return self._targets[(level, target_id)].entered.get(state.value)

    def transition_seq(self, level: Level, target_id: str, state: StrEnum) -> int | None:
        """Sequence number of the latest transition into ``state``, or ``None`` without one."""
        return self._targets[(level, target_id)].entered_seq.get(state.value)

    def previous_state(self, level: Level, target_id: str) -> StrEnum | None:
        """The state a paused or failed target left, which is where resuming sends it back."""
        stack = self._targets[(level, target_id)].stack
        return stack[-1] if stack else None

    def parent_of(self, level: Level, target_id: str) -> str | None:
        return self._targets[(level, target_id)].parent_id

    def children_of(self, level: Level, parent_id: str) -> list[str]:
        return [
            target_id
            for (lvl, target_id), target in self._targets.items()
            if lvl is level and target.parent_id == parent_id
        ]

    def effect_of(self, operation_id: str) -> dict[str, JsonValue] | None:
        """Payload of the effect that closed the operation, or ``None`` if it is not closed."""
        closed = self._closed.get(operation_id)
        return None if closed is None else dict(closed)

    @property
    def open_operations(self) -> Mapping[str, OpenOperation]:
        return dict(self._open)

    def leftover_processes(self) -> list[ProcessIdentity]:
        """Processes started by operations that never recorded an effect."""
        return [op.process for op in self._open.values() if op.process is not None]

    def validate(self, event: Event) -> None:
        self._check_envelope(event)
        match event:
            case TransitionEvent():
                self._plan_transition(event)
            case IntentEvent():
                self._check_intent(event)
            case EffectEvent():
                self._check_effect(event)
            case ProgressEvent():
                self._check_progress(event)

    def apply(self, event: Event) -> None:
        self.validate(event)
        match event:
            case TransitionEvent():
                move = self._plan_transition(event)
                payload = dict(event.payload)
                if move.target is None:
                    self._targets[(event.level, event.target_id)] = _Target(
                        move.new_state,
                        event.parent_id,
                        move.new_stack,
                        {move.new_state.value: payload},
                        {move.new_state.value: event.seq},
                    )
                else:
                    move.target.state = move.new_state
                    move.target.stack = move.new_stack
                    move.target.entered[move.new_state.value] = payload
                    move.target.entered_seq[move.new_state.value] = event.seq
            case IntentEvent():
                self._open[event.operation_id] = OpenOperation(
                    event.operation,
                    event.operation_id,
                    event.level,
                    event.target_id,
                    event.seq,
                    payload=dict(event.payload),
                )
            case EffectEvent():
                del self._open[event.operation_id]
                self._closed[event.operation_id] = dict(event.payload)
            case ProgressEvent():
                if event.payload.get("kind") == PROCESS_STARTED and event.operation_id:
                    pid, token = event.payload.get("pid"), event.payload.get("creation_token")
                    if isinstance(pid, int) and (token is None or isinstance(token, int)):
                        self._open[event.operation_id].process = ProcessIdentity(pid, token)
        self.last_seq = event.seq
        self.last_hash = event.hash

    def _check_envelope(self, event: Event) -> None:
        if event.run_id != self.run_id:
            raise InvalidEvent(f"event belongs to run {event.run_id!r}, not {self.run_id!r}")
        if event.seq != self.last_seq + 1:
            raise InvalidEvent(f"expected seq {self.last_seq + 1}, got {event.seq}")
        if event.prev != self.last_hash:
            raise InvalidEvent("prev does not match the hash of the previous event")

    def _plan_transition(self, event: TransitionEvent) -> _Move:
        level, machine = event.level, MACHINES[event.level]
        try:
            destination = machine.state_type(event.to_state)
        except ValueError:
            raise InvalidEvent(f"{event.to_state!r} is not a {level.value} state") from None

        if event.from_state is None:
            self._check_creation(event, destination)
            return _Move(None, destination, [])

        target = self._targets.get((level, event.target_id))
        if target is None:
            raise InvalidEvent(f"unknown {level.value} {event.target_id!r}")
        try:
            origin = machine.state_type(event.from_state)
        except ValueError:
            raise InvalidEvent(f"{event.from_state!r} is not a {level.value} state") from None
        if origin != target.state:
            raise InvalidEvent(
                f"{level.value} {event.target_id!r} is {target.state.value}, not {origin.value}"
            )

        resumable_origin = machine.is_resumable(origin)
        previous = target.stack[-1] if target.stack else None
        machine.validate(origin, destination, previous=previous)

        stack = list(target.stack)
        if resumable_origin and destination not in machine.table[origin]:
            stack.pop()  # went back to the previous state
        elif machine.is_resumable(destination):
            stack.append(origin)
        elif machine.is_terminal(destination):
            stack.clear()
        return _Move(target, destination, stack)

    def _check_creation(self, event: TransitionEvent, destination: StrEnum) -> None:
        level, machine = event.level, MACHINES[event.level]
        if (level, event.target_id) in self._targets:
            raise InvalidEvent(f"{level.value} {event.target_id!r} already exists")
        if destination != machine.initial:
            raise InvalidEvent(
                f"a new {level.value} must start in {machine.initial.value}, "
                f"not {destination.value}"
            )
        if level is Level.EXECUTION:
            if event.target_id != self.run_id or event.parent_id is not None:
                raise InvalidEvent("the execution is the run itself and has no parent")
            return
        if event.parent_id is None:
            raise InvalidEvent(f"a new {level.value} needs a parent_id")
        if (_PARENT_LEVEL[level], event.parent_id) not in self._targets:
            raise InvalidEvent(
                f"parent {_PARENT_LEVEL[level].value} {event.parent_id!r} does not exist"
            )

    def _check_intent(self, event: IntentEvent) -> None:
        if (event.level, event.target_id) not in self._targets:
            raise InvalidEvent(f"unknown {event.level.value} {event.target_id!r}")
        if event.operation_id in self._open or event.operation_id in self._closed:
            raise InvalidEvent(f"operation {event.operation_id!r} was already used")

    def _check_effect(self, event: EffectEvent) -> None:
        opened = self._open.get(event.operation_id)
        if opened is None:
            raise InvalidEvent(f"effect without an open intent: {event.operation_id!r}")
        if (opened.operation, opened.level, opened.target_id) != (
            event.operation,
            event.level,
            event.target_id,
        ):
            raise InvalidEvent(f"effect does not match the intent of {event.operation_id!r}")

    def _check_progress(self, event: ProgressEvent) -> None:
        if event.payload.get("kind") != PROCESS_STARTED:
            return
        if event.operation_id is None or event.operation_id not in self._open:
            raise InvalidEvent("process_started needs an open operation")
