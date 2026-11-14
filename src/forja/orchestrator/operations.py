"""The diary of an execution: moving targets between states and running operations exactly once.

Every method looks at the log first and skips what it already records, so the same code runs the
first time and after any interruption. Operation ids are ``<name>:<subject>``, with ``:<n>`` after
an operation the resume decided to redo (``n`` counts the redos).
"""

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import JsonValue

from forja.core.events import EventLog, Ref
from forja.core.idempotency import Decision, OperationName
from forja.core.states import Level
from forja.orchestrator.budget import SESSION_ENDED, SESSION_STARTED

Hook = Callable[[str], None]


def _no_hook(_point: str) -> None:
    return None


@dataclass(frozen=True)
class Step:
    """One external operation: what its intent records and how it is done.

    ``payload`` runs only if the step is going to run, because it reads the world as it is right
    then (a commit to expect, a manifest to compare with). ``run`` must be safe to repeat after a
    ``redo`` verdict.
    """

    payload: Callable[[], Awaitable[dict[str, JsonValue]]]
    run: Callable[[], Awaitable[None]]

    @classmethod
    def plain(cls, payload: dict[str, JsonValue], run: Callable[[], Awaitable[None]]) -> "Step":
        """A step whose intent is known in advance."""

        async def constant() -> dict[str, JsonValue]:
            return payload

        return cls(constant, run)


class HookPoint(StrEnum):
    AFTER_INTENT = "after_intent"
    BEFORE_EFFECT = "before_effect"


class StillOpen(Exception):
    """An operation is open in the log: the resume has not settled it yet."""


class Journal:
    """Writes to one run's log and never repeats what the log already says happened.

    ``hook`` is called with ``<operation>:after_intent`` and ``<operation>:before_effect`` so a
    test can stop the process at exactly those points.
    """

    def __init__(self, log: EventLog, *, hook: Hook = _no_hook) -> None:
        self.log = log
        self._hook = hook

    @property
    def run_id(self) -> str:
        return self.log.state.run_id

    # -- targets --------------------------------------------------------------------------------

    def enter(
        self,
        level: Level,
        target: str,
        to: Any,
        *,
        parent: str | None = None,
        payload: dict[str, JsonValue] | None = None,
        refs: tuple[Ref, ...] = (),
    ) -> bool:
        """Move ``target`` into ``to`` unless the log shows it was there; True if it moved.

        A state is entered at most once per target in the flows that use this; the paused and
        failed states, which can be entered again, are moved with ``log.append_transition``.
        """
        state = self.log.state
        if not state.has_target(level, target):
            self.log.append_transition(
                level, target, None, to, parent_id=parent, payload=payload, refs=refs
            )
            return True
        if state.transition_payload(level, target, to) is None:
            current = state.state_of(level, target)
            self.log.append_transition(level, target, current, to, payload=payload, refs=refs)
            return True
        return False

    # -- operations -----------------------------------------------------------------------------

    def operation_id(self, name: OperationName, subject: str) -> str | None:
        """The id to run ``name`` on ``subject`` under, or ``None`` if it already happened."""
        retry = 0
        while True:
            candidate = f"{name}:{subject}" + (f":{retry}" if retry else "")
            effect = self.log.state.effect_of(candidate)
            if effect is None:
                if candidate in self.log.state.open_operations:
                    raise StillOpen(f"{candidate} is still open; resume first")
                return candidate
            if effect.get("reconciled") != Decision.REDO.value:
                return None
            retry += 1

    def done(self, name: OperationName, subject: str) -> bool:
        return self.operation_id(name, subject) is None

    def intent_of(self, name: OperationName, subject: str) -> dict[str, JsonValue]:
        """What the intent of ``name`` on ``subject`` recorded, read from the one that happened;
        empty if none did. It is what a resumed run knows of an operation it does not repeat."""
        state = self.log.state
        retry = 0
        while True:
            candidate = f"{name}:{subject}" + (f":{retry}" if retry else "")
            effect = state.effect_of(candidate)
            if effect is None:
                return {}
            if effect.get("reconciled") != Decision.REDO.value:
                return state.intent_of(candidate) or {}
            retry += 1

    async def operate(
        self, name: OperationName, level: Level, target: str, subject: str, step: Step
    ) -> bool:
        """Intent, work, effect. Returns False, doing nothing, if the log says it happened."""
        op_id = self.operation_id(name, subject)
        if op_id is None:
            return False
        self.log.append_intent(level, target, name, op_id, payload=await step.payload())
        self._hook(f"{name}:{HookPoint.AFTER_INTENT}")
        await step.run()
        self._hook(f"{name}:{HookPoint.BEFORE_EFFECT}")
        self.log.append_effect(level, target, name, op_id)
        return True

    # -- notes ----------------------------------------------------------------------------------

    def note(self, kind: str, payload: dict[str, JsonValue] | None = None) -> None:
        """A display-only fact about the run as a whole (a phase skipped, a wait begun)."""
        self.log.append_progress(Level.EXECUTION, self.run_id, kind, payload=payload)

    def session_started(self) -> None:
        self.note(SESSION_STARTED, {"pid": os.getpid()})

    def session_ended(self, reason: str) -> None:
        self.note(SESSION_ENDED, {"reason": reason})
