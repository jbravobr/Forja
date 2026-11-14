"""Running a tool for a check: one process, one raw log, one record of what happened."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from forja.core.adapters import CommandPlan, ToolOutput, build_spec, read_output_lines
from forja.core.events import EventLog, SinkFactory
from forja.core.gates import Inconclusive, InconclusiveReason
from forja.core.idempotency import OperationName
from forja.core.process import Outcome, ProcessResult, ProcessRunner
from forja.core.states import Level

DEFAULT_TIMEOUT = 300.0
OPERATION = OperationName.RUN_TOOL


@dataclass(frozen=True)
class Execution:
    """A finished tool run: the command, how it ended, and where its output is."""

    check: str
    plan: CommandPlan
    operation_id: str
    result: ProcessResult

    @property
    def finished(self) -> bool:
        """Whether the tool ran to the end by itself, whatever its exit code says."""
        return self.result.outcome is Outcome.EXITED

    @property
    def output(self) -> ToolOutput:
        return ToolOutput(self.result.exit_code, self.result.log_path, self.plan.cwd)

    @property
    def lines(self) -> list[str]:
        return read_output_lines(self.result.log_path)

    def inconclusive(self, what: str) -> Inconclusive:
        """The result for a measurement that could not be made because the tool did not run."""
        program = Path(self.plan.argv[0]).name
        if self.result.outcome is Outcome.TIMED_OUT:
            reason, detail = InconclusiveReason.TIMED_OUT, f"{what}: {program} timed out"
        elif self.result.outcome is Outcome.SPAWN_FAILED:
            reason, detail = InconclusiveReason.TOOL_FAILED, f"{what}: {program} could not start"
        else:
            reason, detail = InconclusiveReason.TOOL_FAILED, f"{what}: {program} was stopped"
        return Inconclusive(reason=reason, detail=detail, tool_run_ids=(self.operation_id,))


class Tools:
    """Runs the commands of one attempt (or of the base) and keeps their logs together."""

    def __init__(
        self,
        *,
        sinks: SinkFactory,
        log: EventLog,
        level: Level,
        target: str,
        logs: Path,
        host_env: Mapping[str, str],
        timeouts: Mapping[str, float] | None = None,
    ) -> None:
        self._sinks = sinks
        self._log = log
        self._level = level
        self._target = target
        self._logs = logs
        self._env = host_env
        self._timeouts = timeouts or {}

    def _fresh_id(self, check: str) -> str:
        """An id no earlier run used, even one cut short, so a repeated judgment starts clean."""
        state = self._log.state
        number = 1
        while True:
            candidate = f"{OPERATION}:{self._target}:{check}:{number}"
            if state.effect_of(candidate) is None and candidate not in state.open_operations:
                return candidate
            number += 1

    async def run(self, check: str, plan: CommandPlan) -> Execution:
        operation_id = self._fresh_id(check)
        timeout = self._timeouts.get(check, DEFAULT_TIMEOUT)
        spec = build_spec(plan, self._env, timeout=timeout)
        sink = self._sinks(self._level, self._target, OPERATION)
        self._logs.mkdir(parents=True, exist_ok=True)
        log_path = self._logs / f"{operation_id.replace(':', '-')}.log"
        result = await ProcessRunner(sink).run(operation_id, spec, log_path)
        return Execution(check, plan, operation_id, result)
