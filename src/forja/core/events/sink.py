from collections.abc import Callable, Mapping

from pydantic import JsonValue

from forja.core.events.log import EventLog
from forja.core.process import EventSink, ProcessIdentity
from forja.core.states import Level

MAX_OUTPUT_LINE_BYTES = 1024

# (level, target id, operation) -> where a process reports to; the orchestrator builds these.
SinkFactory = Callable[[Level, str, str], EventSink]


class LogEventSink:
    """Persists what the process runner reports, as events about one target and operation.

    Output lines are cut to 1 KiB here; the complete text is in the raw log file.
    """

    def __init__(self, log: EventLog, level: Level, target_id: str, operation: str) -> None:
        self._log = log
        self._level = level
        self._target_id = target_id
        self._operation = operation

    def intent(self, operation_id: str, details: Mapping[str, object]) -> None:
        self._log.append_intent(
            self._level, self._target_id, self._operation, operation_id, payload=_json(details)
        )

    def started(self, operation_id: str, identity: ProcessIdentity) -> None:
        self._log.append_progress(
            self._level,
            self._target_id,
            "process_started",
            operation=self._operation,
            operation_id=operation_id,
            payload={"pid": identity.pid, "creation_token": identity.creation_token},
        )

    def progress(self, operation_id: str, channel: str, line: str) -> None:
        shown = line.encode("utf-8")[:MAX_OUTPUT_LINE_BYTES].decode("utf-8", errors="ignore")
        self._log.append_progress(
            self._level,
            self._target_id,
            "output",
            operation=self._operation,
            operation_id=operation_id,
            payload={"channel": channel, "line": shown, "truncated": shown != line},
        )

    def effect(self, operation_id: str, details: Mapping[str, object]) -> None:
        self._log.append_effect(
            self._level, self._target_id, self._operation, operation_id, payload=_json(details)
        )


def _json(details: Mapping[str, object]) -> dict[str, JsonValue]:
    return dict(details)  # type: ignore[arg-type]
